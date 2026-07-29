"""System test: ``/v1/me`` 200/401 split + ``/v1/users`` 401/403/200 split.

This is the repo's first cross-domain API system test (AGENTS.md §4). It
exercises the BFF (django-ninja) + identity domain (services) together through
the Django test client — no real Auth0 call (UAT is deferred to a later slice).

S-01 renamed the URL surface. Phase 5 split the version root from the OIDC
chain: the django-ninja API kept its ``/v1/`` version prefix
(``/v1/me``, ``/v1/users``) but the OIDC redirect chain moved to the URL root
(``/login``, ``/callback``, ``/logout``). The ``bff`` app name + URL names are
unchanged, so ``reverse("bff:callback")`` still resolves — only the path
prefixes moved.

Auth is established via ``client.force_login()`` (Django's test helper) which
populates ``request.user`` — exactly what ``SessionAuth`` reads (research §
"Approach C": SessionAuth trusts ``request.user.is_authenticated`` and never
re-derives from the cookie). Role is derived from ``OWNER_SUB_ID`` via
``test_utils.make_owner`` / ``make_user``.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from src.domains.identity.test_utils import make_owner, make_user


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


def _login_as(client, user) -> None:
    """Authenticate the test client as ``user`` (populates ``request.user``)."""
    client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")


# ---------------------------------------------------------------------------
# /v1/me — 401 anonymous, 200 authed (no sub)
# ---------------------------------------------------------------------------


def test_api_me_returns_401_anonymous(client) -> None:
    """No session → ``session_auth`` falsy → 401."""
    response = client.get("/v1/me")
    assert response.status_code == 401


def test_api_me_returns_200_and_nick_role_for_authed_user(client, user_sub) -> None:
    """Authed → 200 with ``{authenticated, user:{nick, role}}`` and NO ``sub``."""
    user = make_user(sub=user_sub, nick="alice")
    _login_as(client, user)

    response = client.get("/v1/me")
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["user"]["nick"] == "alice"
    assert body["user"]["role"] == "user"
    # Zero Email Storage — no ``sub`` anywhere in the response.
    assert "sub" not in body["user"]
    assert "sub" not in body


# ---------------------------------------------------------------------------
# /v1/users — 401 anonymous, 403 User, 200 [] Owner
# ---------------------------------------------------------------------------


def test_api_users_returns_401_anonymous(client) -> None:
    """Anonymous → ``session_auth`` falsy → 401 (before ``require_owner``)."""
    response = client.get("/v1/users")
    assert response.status_code == 401


def test_api_users_returns_403_for_non_owner(client, owner_sub, user_sub) -> None:
    """Authed User → ``require_owner`` raises ``HttpError(403)``."""
    # ``owner_sub`` fixture sets OWNER_SUB_ID; the user is NOT that sub.
    user = make_user(sub=user_sub, nick="bob")
    _login_as(client, user)

    response = client.get("/v1/users")
    assert response.status_code == 403


def test_api_users_returns_200_for_owner(client, owner_sub) -> None:
    """Authed Owner → 200. The list now carries the owner-admin shape (S-04):
    a paginated ``AdminUserListOut`` whose items DO carry ``sub`` (the owner
    audience needs it to match rows against Auth0). The owner's own row is
    present and flagged ``is_owner``."""
    owner = make_owner(owner_sub)
    _login_as(client, owner)

    response = client.get("/v1/users")
    assert response.status_code == 200
    body = response.json()
    # S-04: paginated envelope.
    assert {"items", "page", "page_size", "total", "total_pages"} <= set(body.keys())
    # The owner's own row is present and flagged.
    assert any(i["nick"] == "test-owner" and i["is_owner"] for i in body["items"])


def test_api_users_owner_list_carries_sub(client, owner_sub, user_sub) -> None:
    """S-04: the owner-admin list is a DIFFERENT audience than ``/v1/me`` — it
    DOES carry ``sub`` so the owner can identify users across nick changes and
    match rows against the Auth0 dashboard. (The sub-less invariant still holds
    on ``/v1/me``'s ``UserOut`` — see ``test_api_me_returns_200_and_nick_role_
    for_authed_user``.)
    """
    make_user(sub=user_sub, nick="carol")
    owner = make_owner(owner_sub)
    _login_as(client, owner)

    response = client.get("/v1/users")
    assert response.status_code == 200
    body = response.json()
    # Every item carries ``sub`` + the ban status + is_owner flag.
    for entry in body["items"]:
        assert "sub" in entry
        assert "ban" in entry
        assert "is_owner" in entry



# ---------------------------------------------------------------------------
# Owner derivation is env-driven (regression guard)
# ---------------------------------------------------------------------------


def test_owner_role_follows_env_not_row(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same row is User or Owner depending solely on ``OWNER_SUB_ID``.

    This is the load-bearing property (research §7): role is never persisted,
    so moving the env var re-roles the row without any DB write. A regression
    that cached role on the row would fail here.
    """
    User = get_user_model()
    user = User.objects.create_user(sub="auth0|floats", nick="dave")

    # Not the owner.
    monkeypatch.setenv("OWNER_SUB_ID", "auth0|someone-else")
    _login_as(client, user)
    assert client.get("/v1/users").status_code == 403

    # Now make them the owner by moving the env var — no row change.
    monkeypatch.setenv("OWNER_SUB_ID", "auth0|floats")
    assert client.get("/v1/users").status_code == 200


# ---------------------------------------------------------------------------
# Session bound to a vanished sub → 401, not 500 (impl-review F2 regression)
# ---------------------------------------------------------------------------


def test_api_me_returns_401_when_session_user_row_gone(client, user_sub) -> None:
    """A session whose sub has no row (S-04 delete, Auth0 tenant migration)
    must return 401 — not 500. The service raises ``DoesNotExist``; the BFF
    maps it to ``HttpError(401)`` to honor the documented contract and force a
    clean re-login.
    """
    user = make_user(sub=user_sub, nick="eve")
    _login_as(client, user)
    user.delete()  # row vanishes while the session is still alive

    response = client.get("/v1/me")
    assert response.status_code == 401


def test_api_users_returns_401_when_session_user_row_gone(
    client, owner_sub, user_sub
) -> None:
    """Same invariant on the owner route's ``require_owner``: a vanished row
    is 401 (re-login), not 500."""
    user = make_user(sub=user_sub, nick="frank")
    _login_as(client, user)
    user.delete()

    response = client.get("/v1/users")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /v1/me — first-login nick prompt (S-01 FR-002)
# ---------------------------------------------------------------------------


def test_patch_me_returns_401_anonymous(client) -> None:
    """No session → ``session_auth`` falsy → 401 (before the body runs)."""
    response = client.patch(
        "/v1/me", data={"nick": "alice"}, content_type="application/json"
    )
    assert response.status_code == 401


def test_patch_me_updates_nick_and_has_set_nick(client, user_sub) -> None:
    """Happy path: 200, nick persisted, ``has_set_nick=True`` reflected in body."""
    user = make_user(sub=user_sub)  # generated shooter-* nick, has_set_nick=False
    _login_as(client, user)

    # Hit ``/`` once so the csrftoken cookie is set (base.html renders {% csrf_token %}).
    client.get("/")
    csrf = client.cookies["csrftoken"].value

    response = client.patch(
        "/v1/me",
        data={"nick": "alice"},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["authenticated"] is True
    assert body["user"]["nick"] == "alice"
    assert body["user"]["has_set_nick"] is True


def test_patch_me_returns_409_on_duplicate_nick(client, user_sub) -> None:
    """A CI-collision maps ``NickTakenError`` → 409, not an opaque 500."""
    make_user(sub="auth0|taken", nick="Bob")
    user = make_user(sub=user_sub)
    _login_as(client, user)
    client.get("/")
    csrf = client.cookies["csrftoken"].value

    response = client.patch(
        "/v1/me",
        data={"nick": "bob"},  # CI-collides with "Bob"
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 409


def test_patch_me_returns_403_without_csrf_token(client, user_sub) -> None:
    """django-ninja's ``SessionAuth`` (extends ``APIKeyCookie``, ``csrf=True``)
    runs ``check_csrf`` *inside* ``_get_key`` — before ``authenticate``. So a
    PATCH with no ``X-CSRFTOKEN`` is 403, even when authenticated.

    This is the load-bearing CSRF invariant the plan calls out: no non-GET
    endpoint existed in F-01, so the auto-CSRF claim was only a docstring
    promise. This test turns it into a tested invariant. (403, not 401, because
    CSRF fires before auth.)

    The Django test client skips CSRF by default (``_dont_enforce_csrf_checks``);
    we opt in via ``enforce_csrf_checks=True`` so ``check_csrf`` actually runs.
    """
    user = make_user(sub=user_sub)
    _login_as(client, user)
    client.handler.enforce_csrf_checks = True

    response = client.patch(
        "/v1/me",
        data={"nick": "alice"},
        content_type="application/json",
        # No HTTP_X_CSRFTOKEN.
    )
    assert response.status_code == 403


def test_patch_me_rejects_invalid_nick(client, user_sub) -> None:
    """An out-of-range nick is rejected with 422 (request-schema validation)."""
    user = make_user(sub=user_sub)
    _login_as(client, user)
    client.get("/")
    csrf = client.cookies["csrftoken"].value

    response = client.patch(
        "/v1/me",
        data={"nick": "x" * 65},  # over 64 chars
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# OIDC redirect chain lives at the URL root (/login, /callback, /logout).
# Phase 5 dropped the /v1 prefix from the OIDC chain; /v1/* is the version root
# for the ninja API only (/v1/me, /v1/users).
#
# The routing contract is pinned via ``reverse()`` — the canonical Django way to
# assert a URL name → path mapping. We deliberately do NOT drive the views here:
# ``login_view`` issues a real OAuth ``authorize_redirect`` (network I/O to
# Auth0's discovery doc), and ``callback`` raises ``MismatchingStateError``
# without a real state token. Those are integration concerns for Phase 4's
# real-Auth0 manual gate, not routing invariants.
# ---------------------------------------------------------------------------


def test_oidc_routes_resolve_at_url_root() -> None:
    """The OIDC redirect chain resolves at ``/login``, ``/callback``, ``/logout``
    (Phase 5 dropped the ``/v1`` prefix from the chain).

    ``reverse()`` is the load-bearing assertion: it pins the URL *names*
    (``bff:login`` etc., unchanged) to their new path segments. A regression
    that re-prefixes the chain with ``/v1`` would fail here.
    """
    from django.urls import reverse

    assert reverse("bff:login") == "/login"
    assert reverse("bff:callback") == "/callback"
    assert reverse("bff:logout") == "/logout"


def test_v1_login_is_404(client) -> None:
    """``/v1/login`` is gone (Phase 5 moved the OIDC chain to the URL root).

    Regression guard: ``/v1/`` stays the version root for the ninja API only
    (``/v1/me``, ``/v1/users``); the OIDC chain no longer lives under it.
    """
    response = client.get("/v1/login")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /logout — CSRF-enforced, clears session (S-01 closes F-01's GET-logout)
# ---------------------------------------------------------------------------


def test_logout_rejects_get(client, user_sub) -> None:
    """GET /logout is no longer accepted (405) — logout is POST + CSRF now.

    F-01 shipped a GET logout (template simplicity); S-01's SPA POSTs instead
    (plan-review F5: GET-logout is a CSRF-soft vector). A regression that
    re-exposed GET logout would fail here.
    """
    user = make_user(sub=user_sub, nick="alice")
    _login_as(client, user)

    response = client.get("/logout")
    assert response.status_code == 405


def test_logout_returns_403_without_csrf_token(client, user_sub) -> None:
    """POST /logout without ``X-CSRFTOKEN`` → 403 (``@csrf_protect``).

    Same load-bearing CSRF invariant as PATCH /v1/me, on the plain Django view
    (``@require_POST`` + ``@csrf_protect`` — not a ninja route).
    """
    user = make_user(sub=user_sub, nick="alice")
    _login_as(client, user)
    client.handler.enforce_csrf_checks = True

    response = client.post("/logout")
    assert response.status_code == 403


def test_logout_post_clears_session(client, user_sub) -> None:
    """POST /logout with a valid CSRF token clears the Django session.

    The view also redirects to Auth0 ``/v2/logout``; without Auth0 creds the
    redirect target is unreachable, but the session is already cleared before
    the redirect. We assert the session-side effect (the load-bearing part)
    and that the response is a 302 (the redirect shape, not its destination).
    """
    user = make_user(sub=user_sub, nick="alice")
    _login_as(client, user)
    assert client.session._session  # session is populated

    # Seed the csrftoken cookie + grab a valid token.
    client.get("/")
    csrf = client.cookies["csrftoken"].value

    response = client.post("/logout", HTTP_X_CSRFTOKEN=csrf)
    assert response.status_code == 302
    # Session cleared before the Auth0 redirect is issued.
    assert not client.session._session


def test_callback_returns_400_on_token_exchange_failure(client, monkeypatch) -> None:
    """A token-exchange failure (state mismatch / CSRF / expiry / signature)
    returns a 400 with a retry link — not a raw 500.

    Fail-closed is preserved either way: no session is created, no user row is
    mutated. The handler improves UX so an expired callback surfaces "Login
    session expired" instead of a generic server error. Mocks Auth0's
    ``authorize_access_token`` to raise the documented Authlib base exception.
    """
    from authlib.oauth2 import OAuth2Error

    from src.bff.routers import auth_routes as auth_routes_mod

    def _raise(_request):
        raise OAuth2Error("state mismatch (fixture)")

    monkeypatch.setattr(
        auth_routes_mod.oauth.auth0,
        "authorize_access_token",
        _raise,
    )

    # No user row before — fail-closed must keep that invariant.
    User = get_user_model()
    assert User.objects.count() == 0

    response = client.get("/callback")

    assert response.status_code == 400
    # The retry link points at the login route.
    assert b'href="/login"' in response.content
    # No session was created, no user row was inserted.
    assert not client.session._session
    assert User.objects.count() == 0
