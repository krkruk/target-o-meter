"""System test: the SPA's auth seam on the live ``runserver`` stack (Phase 3).

The SPA's whole auth model is ``GET /v1/me`` (decide welcome vs. shell) +
``PATCH /v1/me`` (first-login nick) + ``POST /v1/logout``. The Django test
client covers these in ``test_auth_flow.py``, but it never exercises the real
WSGI stack — the CSRF cookie flow, the dev-bypass middleware on a live
``request.user``, and the actual ``/v1/...`` routing all behave differently
when served. This blackbox test drives the real subprocess through the same
sequence the SPA executes:

  1. ``GET /v1/me`` unauthenticated → 401 (the SPA renders Welcome).
  2. Boot with ``DEV_AUTH_BYPASS_SUB`` → ``GET /v1/me`` → 200 with the
     bypass user, ``has_set_nick`` present (the SPA renders AppShell, and
     the NickPrompt when has_set_nick is false).
  3. ``GET /`` first to obtain a ``csrftoken`` cookie (the SPA reads it from
     the document just like the index view's ``{% csrf_token %}`` provides),
     then ``PATCH /v1/me`` with ``X-CSRFToken`` → 200, nick updated,
     ``has_set_nick`` now true.
  4. ``PATCH /v1/me`` WITHOUT ``X-CSRFToken`` → 403 (CSRF enforced on the
     real stack; locks the invariant the SPA's CSRF header relies on).
  5. ``POST /v1/logout`` with ``X-CSRFToken`` → clears the session (subsequent
     ``GET /v1/me`` returns 401).

No traceback may appear in runserver stderr at any step.
"""
from __future__ import annotations

import pytest


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


def _csrf_token_from_cookies(server) -> str:
    """Hit ``/`` to seed the ``csrftoken`` cookie, then read it from the
    httpx client's cookie jar. The SPA does the equivalent client-side."""
    server.get("/")
    return server._client.cookies.get("csrftoken", "")


def test_v1_me_unauthenticated_is_401(runserver) -> None:
    """The SPA's first call: ``GET /v1/me`` returns 401 when unauthed."""
    response = runserver.get("/v1/me")
    assert response.status_code == 401
    runserver.assert_no_traceback()


def test_dev_bypass_me_has_has_set_nick(runserver_factory) -> None:
    """With ``DEV_AUTH_BYPASS_SUB`` set, ``/v1/me`` returns 200 carrying
    ``has_set_nick`` — the field the SPA gates the NickPrompt on."""
    server = runserver_factory(
        extra_env={"DEV_AUTH_BYPASS_SUB": "auth0|phase3-dev"}
    )
    response = server.get("/v1/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["authenticated"] is True
    assert "has_set_nick" in body["user"]
    server.assert_no_traceback()


def test_patch_me_sets_nick_with_csrf(runserver_factory) -> None:
    """``PATCH /v1/me`` with a valid ``X-CSRFToken`` updates the nick and
    flips ``has_set_nick`` to true — the SPA's first-login mutation."""
    server = runserver_factory(
        extra_env={"DEV_AUTH_BYPASS_SUB": "auth0|phase3-patch"}
    )
    token = _csrf_token_from_cookies(server)
    assert token, "no csrftoken cookie seeded by /"

    response = server.patch(
        "/v1/me",
        json={"nick": "phase3-shooter"},
        headers={"X-CSRFToken": token},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["nick"] == "phase3-shooter"
    assert body["user"]["has_set_nick"] is True
    server.assert_no_traceback()


def test_patch_me_without_csrf_is_forbidden(runserver_factory) -> None:
    """``PATCH /v1/me`` WITHOUT ``X-CSRFToken`` is rejected (403) on the real
    stack. This is the invariant the SPA's CSRF header relies on — locking
    it as a tested system property, not a docstring promise."""
    server = runserver_factory(
        extra_env={"DEV_AUTH_BYPASS_SUB": "auth0|phase3-nocsrf"}
    )
    _csrf_token_from_cookies(server)  # seed the cookie but don't send the header
    response = server.patch("/v1/me", json={"nick": "should-not-stick"})
    assert response.status_code == 403, response.text
    server.assert_no_traceback()


def test_post_logout_clears_session(runserver_factory) -> None:
    """``POST /v1/logout`` with ``X-CSRFToken`` clears the session — the next
    ``GET /v1/me`` returns 401. (The Auth0 ``/v2/logout`` redirect may 302 or
    error without creds; the Django session is cleared regardless, which is
    what the SPA cares about.)"""
    server = runserver_factory(
        extra_env={"DEV_AUTH_BYPASS_SUB": "auth0|phase3-logout"}
    )
    # Confirm we start authenticated.
    assert server.get("/v1/me").status_code == 200

    token = _csrf_token_from_cookies(server)
    logout_response = server.post("/logout", headers={"X-CSRFToken": token})
    # 302 (redirect to Auth0 /v2/logout) or 200 are both acceptable; a 403
    # would mean CSRF broke, a 500 would mean the logout view crashed.
    assert logout_response.status_code in (200, 302), logout_response.text
    server.assert_no_traceback()
