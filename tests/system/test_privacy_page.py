"""System test: ``/privacy`` serves a standalone cookie-policy page.

The cookie consent banner (Phase 3) links its "Cookie Policy" anchor here, so
the route MUST resolve to a real server-rendered page — not be swallowed by the
SPA-shell catch-all (which would boot React and render ``Welcome`` for
unauthenticated visitors, hiding the policy text behind the auth seam).

The page documents the strictly-necessary cookies in use (``sessionid`` +
``csrftoken``) and states that no analytics/marketing cookies are present. It is
a standalone HTML document that does NOT extend ``base.html`` and does NOT mount
the SPA.
"""

from __future__ import annotations

import pytest

from src.domains.identity.test_utils import make_user


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


def test_privacy_returns_200_anonymous(client) -> None:
    """``/privacy`` is reachable unauthenticated (the policy is public)."""
    response = client.get("/privacy")
    assert response.status_code == 200


def test_privacy_lists_strictly_necessary_cookies(client) -> None:
    """The page names the two strictly-necessary cookies in use."""
    response = client.get("/privacy")
    body = response.content.decode()
    assert "sessionid" in body
    assert "csrftoken" in body


def test_privacy_states_no_analytics(client) -> None:
    """The page states that no analytics/marketing cookies are used."""
    response = client.get("/privacy")
    body = response.content.decode().lower()
    assert "analytics" in body
    assert "no " in body  # "no analytics" / "no marketing" wording


def test_privacy_is_not_the_spa_shell(client) -> None:
    """The page is standalone: it must NOT carry the SPA root mount point.

    A regression that routes ``/privacy`` through the SPA shell (the catch-all)
    would fail here — the shell serves ``<div id="root">`` and the vite entry
    tag, proving React would boot and hide the policy behind the auth seam.
    """
    response = client.get("/privacy")
    assert b'<div id="root">' not in response.content
    assert b"src/main.tsx" not in response.content


def test_privacy_is_identical_authed_and_unauthed(client, user_sub) -> None:
    """The route is public — no auth gate — so it renders the same either way."""
    anon = client.get("/privacy").content

    user = make_user(sub=user_sub, nick="bob")
    client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
    authed = client.get("/privacy").content

    assert anon == authed


def test_privacy_typo_still_404s(client) -> None:
    """The catch-all exclusion is specific to ``privacy``, not a prefix:
    ``/privacy-typos`` is an unknown path and must surface as 404 (not a false
    200 from the SPA shell that would mask the routing bug).
    """
    response = client.get("/privacy-typos")
    assert response.status_code == 404
