"""OIDC redirect chain — login, callback, logout.

Server-side BFF views (AGENTS.md §5 — only ``src/bff/`` handles HTTP). Tokens
stay server-side; the browser carries only Django's ``sessionid``.

  - ``login``    → validates ``next`` (open-redirect prevention), stashes it in
                   the session, redirects to Auth0 ``/authorize``.
  - ``callback`` → Authlib validates the token (signature/iss/aud/nonce/exp),
                   resolves/creates the ``User`` row by ``sub``, calls
                   ``django.contrib.auth.login``, redirects to ``next``.
  - ``logout``   → clears the Django session, redirects to Auth0 ``/v2/logout``.

Critical implementation details (see plan §"Critical Implementation Details"):
  - ``user.backend`` must be set before ``login()`` so the session records a
    valid backend (we call ``login()`` without ``authenticate()`` — Auth0
    already proved identity; nothing to check a password against).
  - ``next`` is allowlisted via ``url_has_allowed_host_and_scheme`` — never
    redirect to an arbitrary user-supplied URL.
  - ``returnTo`` is ``quote_plus``-encoded; mis-encoding makes Auth0 silently
    fall back to the first Allowed Logout URL.
"""

from __future__ import annotations

import logging

from authlib.jose.errors import JoseError
from authlib.oauth2 import OAuth2Error
from django.conf import settings
from django.contrib.auth import login
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from ninja import Router

from src.bff.oauth import oauth
from src.domains.identity.services import get_or_create_user_row


logger = logging.getLogger("target_o_meter.auth")


router = Router()


def _safe_next(request: HttpRequest, next_url: str | None) -> str:
    """Validate ``next`` against the host — open-redirect prevention.

    Falls back to ``"/"`` when missing or unsafe. ``url_has_allowed_host_and
    _scheme`` is Django's canonical helper: it rejects cross-host URLs unless
    the host is in ``ALLOWED_HOSTS`` (so a ``?next=//evil.com`` payload cannot
    redirect off-site).
    """
    if not next_url:
        return "/"
    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return "/"


def login_view(request: HttpRequest) -> HttpResponse:
    """Redirect to Auth0 ``/authorize``. Stashes ``next`` in the session.

    Named ``login_view`` (not ``login``) to avoid shadowing Django's
    ``contrib.auth.login`` imported below for the callback.
    """
    next_url = _safe_next(request, request.GET.get("next"))
    request.session["oauth_next"] = next_url
    redirect_uri = request.build_absolute_uri(reverse("bff:callback"))
    return oauth.auth0.authorize_redirect(request, redirect_uri=redirect_uri)


def callback(request: HttpRequest) -> HttpResponse:
    """Auth0 redirects here with ``?code``. Exchange for tokens, log in.

    Authlib's ``authorize_access_token`` auto-validates signature/iss/aud/
    nonce/exp. On a tampered/replayed/expired callback it raises
    ``OAuth2Error`` (state/CSRF/expiry mismatch) or ``JoseError`` (token
    signature/claims failure); we catch both and return a friendly 400 with
    a retry link instead of letting Django surface a raw 500. Fail-closed is
    preserved either way: no session is created, no row is mutated. On
    success we resolve-or-create the ``User`` by ``sub`` and call Django's
    ``login()``.
    """
    try:
        token = oauth.auth0.authorize_access_token(request)
    except (OAuth2Error, JoseError) as exc:
        # State mismatch / CSRF / expired-code / token-validation failure.
        # Log the typed exception (DEBUG=False hides the traceback from the
        # user) and fail closed with a retry link — not a redirect, since a
        # stale ``state`` could loop straight back here.
        logger.warning("Auth0 token exchange failed: %s", exc)
        login_url = reverse("bff:login")
        return HttpResponse(
            "Login session expired or could not be verified. "
            f'<a href="{login_url}">Try logging in again</a>.',
            status=400,
        )
    userinfo = token.get("userinfo", {})
    sub = userinfo.get("sub")
    if not sub:
        # Should be unreachable — OIDC mandates ``sub``. Fail loudly rather
        # than creating a row with an empty key (which UserManager rejects).
        return HttpResponse("OIDC response missing sub", status=400)

    # Resolve-or-create the User row via the service (AGENTS.md §5 — BFF never
    # imports ``identity.User``). The service returns the ORM row (``login()``
    # is keyed on the model instance) plus ``is_first_login_ever`` for owner-
    # bootstrap logging.
    user, is_first_login_ever = get_or_create_user_row(sub)

    # Phase 5.D owner bootstrap: Auth0's ``sub`` is opaque, so the owner can't
    # pre-state ``OWNER_SUB_ID``. On the FIRST-EVER login (no users existed
    # before this call), log at WARNING with the literal ``sub`` and a ready-
    # to-paste instruction. The owner copies the sub into ``.env`` as
    # ``OWNER_SUB_ID``, restarts, and from the next login on ``User.role``
    # derives ``OWNER`` via the existing env comparison. No DB change — the
    # role-never-persisted invariant (research §7) is intact.
    if is_first_login_ever:
        logger.warning(
            "FIRST LOGIN — set OWNER_SUB_ID=%s in .env and restart to make "
            "this user the Owner. (sub=%s)",
            sub,
            sub,
        )
    else:
        logger.info("Login successful (sub=%s)", sub)

    # ``login()`` without ``authenticate()``: Auth0 already proved identity, so
    # there's no password to check. We must set ``user.backend`` so the session
    # records a valid backend (Critical Implementation Details).
    user.backend = "django.contrib.auth.backends.ModelBackend"
    login(request, user)

    next_url = request.session.pop("oauth_next", "/")
    return redirect(next_url)


@require_POST
@csrf_protect
def logout(request: HttpRequest) -> HttpResponse:
    """Clear the Django session, then redirect to Auth0 ``/v2/logout``.

    POST + CSRF (S-01). F-01 shipped this as a GET view (template simplicity);
    the plan-review F5 flagged GET-logout as a CSRF-soft vector, so the SPA now
    POSTs with an ``X-CSRFTOKEN`` header. Kept as a plain Django view (not a
    ninja ``Router`` route) so the OIDC chain stays in one shape —
    ``login_view`` and ``callback`` are plain Django views too, registered
    top-level in ``urls.py``. ``@csrf_protect`` is explicit intent on top of
    ``CsrfViewMiddleware`` (belt-and-suspenders); ``@require_POST`` rejects GET
    with 405.

    The session is cleared *before* the Auth0 redirect is issued, so even if
    Auth0 is unreachable (no creds in dev), the Django side is already logged
    out.
    """
    request.session.clear()
    from urllib.parse import quote_plus, urlencode

    return_url = request.build_absolute_uri(reverse("bff:index"))
    params = urlencode(
        {"returnTo": return_url, "client_id": settings.AUTH0_CLIENT_ID},
        quote_via=quote_plus,
    )
    return redirect(f"https://{settings.AUTH0_DOMAIN}/v2/logout?{params}")
