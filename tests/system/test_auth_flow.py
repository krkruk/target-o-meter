"""System test: ``/api/me`` 200/401 split + ``/api/users`` 401/403/200 split.

This is the repo's first cross-domain API system test (AGENTS.md §4). It
exercises the BFF (django-ninja) + identity domain (services) together through
the Django test client — no real Auth0 call (UAT is deferred to a later slice).

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
# /api/me — 401 anonymous, 200 authed (no sub)
# ---------------------------------------------------------------------------

def test_api_me_returns_401_anonymous(client) -> None:
    """No session → ``session_auth`` falsy → 401."""
    response = client.get("/api/me")
    assert response.status_code == 401


def test_api_me_returns_200_and_nick_role_for_authed_user(client, user_sub) -> None:
    """Authed → 200 with ``{authenticated, user:{nick, role}}`` and NO ``sub``."""
    user = make_user(sub=user_sub, nick="alice")
    _login_as(client, user)

    response = client.get("/api/me")
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["user"]["nick"] == "alice"
    assert body["user"]["role"] == "user"
    # Zero Email Storage — no ``sub`` anywhere in the response.
    assert "sub" not in body["user"]
    assert "sub" not in body


# ---------------------------------------------------------------------------
# /api/users — 401 anonymous, 403 User, 200 [] Owner
# ---------------------------------------------------------------------------

def test_api_users_returns_401_anonymous(client) -> None:
    """Anonymous → ``session_auth`` falsy → 401 (before ``require_owner``)."""
    response = client.get("/api/users")
    assert response.status_code == 401


def test_api_users_returns_403_for_non_owner(client, owner_sub, user_sub) -> None:
    """Authed User → ``require_owner`` raises ``HttpError(403)``."""
    # ``owner_sub`` fixture sets OWNER_SUB_ID; the user is NOT that sub.
    user = make_user(sub=user_sub, nick="bob")
    _login_as(client, user)

    response = client.get("/api/users")
    assert response.status_code == 403


def test_api_users_returns_200_for_owner(client, owner_sub) -> None:
    """Authed Owner → 200. The list contains whatever users exist (here, the
    owner's own row — ``list_users`` returns all rows, no ``sub`` on any)."""
    owner = make_owner(owner_sub)
    _login_as(client, owner)

    response = client.get("/api/users")
    assert response.status_code == 200
    body = response.json()
    # The owner's own row is present (make_owner seeded it).
    assert any(u["nick"] == "test-owner" and u["role"] == "owner" for u in body)
    # And no entry exposes ``sub``.
    for entry in body:
        assert "sub" not in entry


def test_api_users_200_entries_carry_no_sub(client, owner_sub, user_sub) -> None:
    """When the owner lists users, no entry exposes ``sub`` (Zero Email Storage).

    Seeds a non-owner row so the list is non-empty, then asserts the response
    shape omits ``sub`` at every level.
    """
    make_user(sub=user_sub, nick="carol")
    owner = make_owner(owner_sub)
    _login_as(client, owner)

    response = client.get("/api/users")
    assert response.status_code == 200
    for entry in response.json():
        assert "sub" not in entry
        assert set(entry.keys()) == {"nick", "role"}


# ---------------------------------------------------------------------------
# Owner derivation is env-driven (regression guard)
# ---------------------------------------------------------------------------

def test_owner_role_follows_env_not_row(client, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert client.get("/api/users").status_code == 403

    # Now make them the owner by moving the env var — no row change.
    monkeypatch.setenv("OWNER_SUB_ID", "auth0|floats")
    assert client.get("/api/users").status_code == 200
