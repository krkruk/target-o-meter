"""S-04 ban enforcement at the OAuth callback (login-only enforcement).

The callback is the single enforcement point: after resolving the user, before
``login()``, an active ban blocks the session creation and renders the banned
page. The OAuth token exchange is mocked (no real Auth0) — mirrors
``test_owner_bootstrap_logging.py``'s ``_patch_oauth_token``.

Three cases:
  - active ban → ``banned.html`` rendered + NO session created.
  - no ban → session created + redirect to ``/`` (the existing happy path).
  - expired ban (``banned_until`` in the past) → logs in normally (not blocked).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from src.domains.identity.test_utils import make_ban, make_user


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


def _patch_oauth_token(monkeypatch: pytest.MonkeyPatch, sub: str) -> None:
    """Patch ``oauth.auth0.authorize_access_token`` to return a fixed userinfo.

    Avoids the real Auth0 round-trip; the callback reads
    ``token['userinfo']['sub']``.
    """
    from src.bff.routers import auth_routes as auth_routes_mod

    def _fake_authorize_access_token(_request):
        return {"userinfo": {"sub": sub}}

    monkeypatch.setattr(
        auth_routes_mod.oauth.auth0,
        "authorize_access_token",
        _fake_authorize_access_token,
    )


def test_active_ban_blocks_login_and_renders_banned_page(client, monkeypatch) -> None:
    """An active ban → no session + the banned page with the reason + expiry."""
    sub = "auth0|banned-user"
    make_user(sub=sub, nick="banned-alice")
    make_ban(
        user=__import__(
            "src.domains.identity.models", fromlist=["User"]
        ).User.objects.get(sub=sub),
        reason="toxic behavior on the range",
        banned_until=timezone.now() + timedelta(days=7),
    )
    _patch_oauth_token(monkeypatch, sub)

    response = client.get("/callback")

    # The banned page is served (200, not a 302 redirect to the app).
    assert response.status_code == 200
    assert b"You are banned" in response.content or b"banned" in response.content.lower()
    assert b"toxic behavior on the range" in response.content
    # No session was created.
    assert not client.session._session


def test_unbanned_user_logs_in_normally(client, monkeypatch) -> None:
    """No ban → session created + redirect to ``/`` (the existing happy path)."""
    sub = "auth0|clean-user"
    make_user(sub=sub, nick="alice")
    _patch_oauth_token(monkeypatch, sub)

    response = client.get("/callback")

    assert response.status_code == 302
    assert client.session._session  # a session was created


def test_expired_ban_does_not_block(client, monkeypatch) -> None:
    """A ban whose ``banned_until`` is past → logs in normally (not blocked)."""
    from src.domains.identity.models import User

    sub = "auth0|expired-ban"
    make_user(sub=sub, nick="alice")
    make_ban(
        user=User.objects.get(sub=sub),
        banned_until=timezone.now() - timedelta(days=1),  # expired
    )
    _patch_oauth_token(monkeypatch, sub)

    response = client.get("/callback")

    assert response.status_code == 302
    assert client.session._session


def test_banned_page_is_standalone_no_spa_root(client, monkeypatch) -> None:
    """The banned page must NOT include the SPA mount (``#root``) or vite tags
    — a banned user must not load the client bundle."""
    sub = "auth0|banned-no-bundle"
    make_user(sub=sub, nick="banned")
    from src.domains.identity.models import User

    make_ban(
        user=User.objects.get(sub=sub),
        reason="five chars reason here",
        banned_until=timezone.now() + timedelta(days=7),
    )
    _patch_oauth_token(monkeypatch, sub)

    response = client.get("/callback")
    assert response.status_code == 200
    assert b'id="root"' not in response.content
    assert b"vite" not in response.content.lower()
