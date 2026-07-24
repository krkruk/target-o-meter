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

from django.conf import settings
from django.contrib.auth import login
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from ninja import Router

from src.bff.oauth import oauth
from src.domains.identity.services import get_or_create_user_by_sub


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
    nonce/exp — if any check fails it raises, and Django returns a 500 (no
    session is created, no row is mutated). On success we resolve-or-create
    the ``User`` by ``sub`` and call Django's ``login()``.
    """
    token = oauth.auth0.authorize_access_token(request)
    userinfo = token.get("userinfo", {})
    sub = userinfo.get("sub")
    if not sub:
        # Should be unreachable — OIDC mandates ``sub``. Fail loudly rather
        # than creating a row with an empty key (which UserManager rejects).
        return HttpResponse("OIDC response missing sub", status=400)

    get_or_create_user_by_sub(sub)

    # Re-fetch the ORM row — login() needs the model instance, not the DTO.
    from src.domains.identity.models import User
    user = User.objects.get(sub=sub)

    # ``login()`` without ``authenticate()``: Auth0 already proved identity, so
    # there's no password to check. We must set ``user.backend`` so the session
    # records a valid backend (Critical Implementation Details).
    user.backend = "django.contrib.auth.backends.ModelBackend"
    login(request, user)

    next_url = request.session.pop("oauth_next", "/")
    return redirect(next_url)


def logout(request: HttpRequest) -> HttpResponse:
    """Clear the Django session, then redirect to Auth0 ``/v2/logout``.

    GET-based in F-01 (template simplicity); S-01's SPA should re-implement as
    POST + CSRF (plan §"What We're NOT Doing" — GET-logout CSRF-soft vector).
    """
    request.session.clear()
    from urllib.parse import quote_plus, urlencode

    return_url = request.build_absolute_uri(reverse("bff:index"))
    params = urlencode(
        {"returnTo": return_url, "client_id": settings.AUTH0_CLIENT_ID},
        quote_via=quote_plus,
    )
    return redirect(f"https://{settings.AUTH0_DOMAIN}/v2/logout?{params}")
