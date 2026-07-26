"""System test: live ``runserver`` smoke + the dev-bypass nick collision (S-01).

The rest of the system suite drives the BFF via the Django test client, which
is fast but never exercises the real request-serving stack. A class of bugs
only surfaces on that path — here we stand up a real ``runserver`` subprocess,
make real HTTP calls, and assert on the server's captured stderr log.

The motivating bug (S-01 manual-smoke regression): ``DevAuthBypassMiddleware``
derives the bypass user's nick as ``f"dev-{sub[:8]}"``. Auth0 subs share the
``auth0|`` prefix (6 of the 8 chars), so distinct subs routinely produce
identical 8-char prefixes and collide on the ``identity_user_nick_ci_unique``
constraint. Booting the server with a ``DEV_AUTH_BYPASS_SUB`` whose derived
nick matches an existing row makes ``get_or_create``'s INSERT raise
``IntegrityError`` on the first request → a 500 on every authed surface.
"""
from __future__ import annotations

import pytest


# These tests do NOT use ``client`` — they drive the live subprocess. ``django_db``
# keeps the marker consistent with the rest of the suite even though no ORM
# access happens inside the test process (the live server owns its own DB).
pytestmark = [pytest.mark.django_db, pytest.mark.dev]


def test_runserver_serves_v1_me_unauthenticated(runserver) -> None:
    """Smoke: the booted server answers ``/v1/me`` with 401 when unauthed.

    Establishes the harness itself works end-to-end before the bug repro — if
    this fails, the problem is the fixture, not the product code.
    """
    response = runserver.get("/v1/me")
    assert response.status_code == 401
    runserver.assert_no_traceback()


def test_dev_bypass_colliding_sub_does_not_500(runserver_factory) -> None:
    """A ``DEV_AUTH_BYPASS_SUB`` whose derived nick collides with an existing
    row MUST NOT raise ``IntegrityError`` on the first request.

    Faithful repro of the S-01 manual-smoke regression:

      1. Seed the DB with a row whose nick is ``dev-auth0|de`` (what
         ``auth0|dev-bypass`` would derive — 8-char prefix ``auth0|de``).
      2. Boot ``runserver`` with ``DEV_AUTH_BYPASS_SUB=auth0|dev-beta`` — a
         distinct sub that derives the SAME nick (``auth0|de`` prefix collides).
      3. Hit ``/v1/me``. Before the fix: the middleware's ``get_or_create``
         INSERT hits the CI-unique constraint → ``IntegrityError`` → 500 +
         traceback in runserver stderr. After the fix: 200 with a nick.

    This is exactly the developer's scenario: two different bypass subs (e.g.
    switching between feature branches) whose ``auth0|``-prefixed 8-char slices
    happen to match.
    """
    # 1. Pre-seed the colliding row. The seed runs against the SAME throwaway
    #    DB the server will boot against (runserver_factory wires them).
    seed = (
        "from src.domains.identity.models import User; "
        "User.objects.create(sub='auth0|dev-bypass', nick='dev-auth0|de')"
    )

    # 2. Boot with the colliding sub.
    server = runserver_factory(
        extra_env={"DEV_AUTH_BYPASS_SUB": "auth0|dev-beta"},
        seed_script=seed,
    )

    # 3. First authed request — before the fix this is a 500.
    response = server.get("/v1/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["authenticated"] is True
    assert body["user"]["nick"]
    server.assert_no_traceback()


def test_dev_bypass_returns_200_when_sub_set(runserver_factory) -> None:
    """Happy path: ``DEV_AUTH_BYPASS_SUB`` set (no collision) → 200 + nick.

    Pairs with the collision repro to prove the bypass itself still works once
    the nick-derivation is collision-free.
    """
    server = runserver_factory(
        extra_env={"DEV_AUTH_BYPASS_SUB": "auth0|solo-dev-sub"},
    )
    response = server.get("/v1/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["authenticated"] is True
    assert body["user"]["nick"]
    server.assert_no_traceback()


def test_dev_bypass_returns_401_when_sub_unset(runserver) -> None:
    """With ``DEV_AUTH_BYPASS_SUB`` unset, the middleware no-ops → 401.

    Belt-and-suspenders alongside ``test_dev_bypass.py`` but on the live stack:
    the bypass is opt-in via env, not on by default.
    """
    response = runserver.get("/v1/me")
    assert response.status_code == 401
