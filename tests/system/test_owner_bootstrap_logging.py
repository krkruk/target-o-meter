"""System test: the callback logs the login's ``sub`` (Phase 5.D owner bootstrap).

The problem Phase 5.D solves: Auth0's ``sub`` is opaque, so the owner can't
pre-state ``OWNER_SUB_ID``. Until now there was no way to discover it short of
inspecting the DB row after a login. Phase 5.D adds logging to ``callback``:

  - The **first-ever** login (no OTHER users exist) logs at ``WARNING`` with a
    ready-to-paste instruction: ``set OWNER_SUB_ID=<sub> in .env and restart``.
  - Subsequent logins log at ``INFO`` with the ``sub``.

The owner reads the WARNING on first login, pastes the ``sub`` into ``.env``,
restarts, and from the second login on ``User.role`` derives
``Role.OWNER`` (via the existing ``OWNER_SUB_ID`` comparison). The
*role-never-persisted* invariant (research §7) is intact — no DB column is
added, no row is mutated; only an env var is set.

The OAuth code-exchange is mocked here (no real Auth0): the test patches
``oauth.auth0.authorize_access_token`` to return a fixture userinfo, then
asserts on the captured logs.
"""
from __future__ import annotations

import logging

import pytest


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


def _patch_oauth_token(monkeypatch: pytest.MonkeyPatch, sub: str) -> None:
    """Patch ``oauth.auth0.authorize_access_token`` to return a fixed userinfo.

    Avoids the real Auth0 round-trip: the callback calls
    ``oauth.auth0.authorize_access_token(request)`` and reads
    ``token['userinfo']['sub']``. We make it return a fixture token carrying
    the bespoke ``sub``.
    """
    from src.bff.routers import auth_routes as auth_routes_mod

    def _fake_authorize_access_token(_request):
        return {"userinfo": {"sub": sub}}

    # ``oauth`` is imported into auth_routes at module load; patch the attribute
    # on the imported registry object.
    monkeypatch.setattr(
        auth_routes_mod.oauth.auth0,
        "authorize_access_token",
        _fake_authorize_access_token,
    )


def test_first_login_logs_sub_at_warning(caplog, client, monkeypatch) -> None:
    """The first-ever login logs at WARNING with a ready-to-paste instruction.

    The warning carries the literal ``sub`` and names ``OWNER_SUB_ID`` so the
    owner can copy it into ``.env`` and restart. After restart that user's next
    login derives ``Role.OWNER`` via the existing env comparison.
    """
    sub = "auth0|first-owner"
    _patch_oauth_token(monkeypatch, sub)

    # Capture at the logger the callback uses, at every level.
    caplog.set_level(logging.DEBUG, logger="target_o_meter.auth")

    response = client.get("/callback")

    # The callback completes the login flow (mocked token → row created → 302).
    assert response.status_code == 302

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, (
        f"expected a WARNING log for the first-ever login, got records: "
        f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )
    msg = warnings[0].getMessage()
    # The sub is in the message (so the owner can copy it).
    assert sub in msg
    # The instruction names OWNER_SUB_ID (the env var to set).
    assert "OWNER_SUB_ID" in msg


def test_subsequent_login_logs_sub_at_info(caplog, client, monkeypatch, user_sub) -> None:
    """A login when OTHER users already exist logs at INFO (not WARNING).

    Pinned so a regression that warns on every login (spammy) or never logs the
    sub (the owner can't discover later subs) fails here.
    """
    from src.domains.identity.test_utils import make_user

    # Pre-seed an existing user → the next login is NOT the first.
    make_user(sub=user_sub, nick="already-here")

    new_sub = "auth0|second-user"
    _patch_oauth_token(monkeypatch, new_sub)

    caplog.set_level(logging.DEBUG, logger="target_o_meter.auth")

    response = client.get("/callback")
    assert response.status_code == 302

    # No WARNING — this is not the first login.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not warnings, (
        f"subsequent login should not WARN; got: "
        f"{[(r.levelname, r.getMessage()) for r in warnings]}"
    )
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert infos, (
        f"expected an INFO log for the login, got records: "
        f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )
    assert new_sub in infos[0].getMessage()
