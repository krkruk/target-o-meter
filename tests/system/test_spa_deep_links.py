"""System test: S-02 Phase 6 SPA deep-link catch-all.

BrowserRouter owns the SPA's routes (/dashboard, /capture, /upload,
/waiting/:jobId, /results/:jobId), so a refresh on any of them must serve the
index document (the SPA shell) — NOT 404. Django serves it via a catch-all
``re_path`` in ``bff/urls.py`` that excludes the versioned API + OIDC chain +
admin (so those still 404 correctly on unknown sub-paths).

This pins both halves of the contract:
  - client-side SPA routes (``/dashboard``, ``/waiting/<uuid>``, ``/results/abc``)
    serve the index document (HTTP 200, the SPA shell HTML).
  - excluded prefixes still 404 on unknown sub-paths (``/v1/nope`` → 404,
    ``/login-typos`` → handled by the OIDC chain, not the catch-all).

The index view renders ``base.html``; the SPA mount point (``<div id="root">``)
is the observable signal that the shell — not an API response — was served.
"""
from __future__ import annotations

import pytest


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


_SPA_ROUTES = [
    "/dashboard",
    "/capture",
    "/upload",
    "/waiting/123e4567-e89b-12d3-a456-426614174000",
    "/results/123e4567-e89b-12d3-a456-426614174000",
    # S-04: the owner admin page is a client-side route; refreshing /admin
    # must serve the SPA shell so BrowserRouter can pick it up. (/admin/ with
    # a trailing slash is Django admin — handled by root urls.py first.)
    "/admin",
]


@pytest.mark.parametrize("route", _SPA_ROUTES)
def test_spa_deep_link_serves_index(client, route: str) -> None:
    """Every SPA client-side route serves the index document on refresh (200 +
    the SPA root mount point), so BrowserRouter can pick the route up client-
    side. A 404 here would break deep-link refresh."""
    response = client.get(route)
    assert response.status_code == 200, response.content
    # The SPA mounts at #root; base.html carries that mount point.
    assert b'id="root"' in response.content, (
        f"SPA shell not served for {route} — expected the root mount point"
    )


def test_v1_unknown_subpath_still_404s(client) -> None:
    """The catch-all must NOT shadow the versioned API: an unknown /v1/... path
    still 404s (not a false 200 from the catch-all). Regression guard for the
    negative-lookahead on the catch-all regex."""
    response = client.get("/v1/does-not-exist")
    assert response.status_code == 404


def test_v1_login_is_404(client) -> None:
    """``/v1/login`` is gone (the OIDC chain moved to /login at the URL root).
    The catch-all must NOT pick it up — /v1/ is excluded."""
    response = client.get("/v1/login")
    assert response.status_code == 404
