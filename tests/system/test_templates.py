"""System test: welcome/login/main template dispatch (Phase 5).

Covers the manual checks:
  - 5.4 ``/`` unauthenticated → welcome with Login button
  - 5.5 Login → main page shows "logged in as {nick} ({role})"
  - 5.6 Logout → returns to welcome

Exercises the Django test client + template rendering. Auth is established via
``force_login`` (the bypass middleware path is covered by test_dev_bypass.py).
"""
from __future__ import annotations

import pytest

from src.domains.identity.test_utils import make_owner, make_user


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


def test_index_anonymous_shows_welcome_with_login_button(client) -> None:
    """5.4: unauthenticated ``/`` → welcome page + Login link to bff:login."""
    response = client.get("/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "Login" in body
    # The Login link points at the OIDC login route.
    assert "/bff/login" in body


def test_index_authenticated_shows_main_with_nick_and_role(
    client, user_sub,
) -> None:
    """5.5: authenticated ``/`` → main page with "logged in as {nick} ({role})"."""
    user = make_user(sub=user_sub, nick="alice")
    client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")

    response = client.get("/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "logged in as alice (user)" in body
    assert "Logout" in body
    assert "/bff/logout" in body


def test_index_shows_owner_role_for_owner(client, owner_sub) -> None:
    """The role token in the main page follows OWNER_SUB_ID (owner case)."""
    owner = make_owner(owner_sub)
    client.force_login(owner, backend="django.contrib.auth.backends.ModelBackend")

    response = client.get("/")
    body = response.content.decode()
    assert "(owner)" in body


def test_logout_link_present_on_main(client, user_sub) -> None:
    """5.6 prep: the main page carries a Logout link to bff:logout.

    The actual logout redirect goes to Auth0 in prod; here we assert the link
    is rendered (the click → welcome round-trip needs a session, tested below).
    """
    user = make_user(sub=user_sub, nick="bob")
    client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")

    response = client.get("/")
    assert "/bff/logout" in response.content.decode()


def test_full_navigation_round_trip(client, user_sub) -> None:
    """5.4 → 5.5 → 5.6: welcome → login → main → logout → welcome.

    Uses ``force_login`` to stand in for the OAuth callback (the real callback
    needs Auth0 creds — deferred to UAT). Proves the template dispatch flips
    correctly with auth state.
    """
    # 5.4 — anonymous sees welcome.
    assert "Login" in client.get("/").content.decode()

    # 5.5 — "log in" (force_login stands in for the callback).
    user = make_user(sub=user_sub, nick="carol")
    client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
    main_body = client.get("/").content.decode()
    assert "logged in as carol (user)" in main_body

    # 5.6 — "log out" clears the session; ``/`` shows welcome again.
    client.logout()
    welcome_body = client.get("/").content.decode()
    assert "Login" in welcome_body
    assert "logged in as" not in welcome_body
