"""System test: ``/`` serves the SPA shell document (S-01).

Replaces the retired ``test_templates.py`` (which asserted substrings in
``welcome.html`` / ``main.html`` — both gone once the SPA lands). S-01 moves
the welcome/authed dispatch client-side: ``/`` serves a single shell document
regardless of auth state, and React decides what to render based on
``GET /v1/me``.

Phase 1 asserted the dispatch collapse + the root mount point; Phase 2 adds the
django-vite entry-tag assertion (``test_index_serves_vite_entry``).
"""

from __future__ import annotations

import re

import pytest

from src.domains.identity.test_utils import make_user


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


_CSRF_TOKEN_RE = re.compile(rb'name="csrfmiddlewaretoken" value="[^"]*"')


def test_index_returns_200_anonymous(client) -> None:
    """``/`` is reachable and returns 200 unauthenticated."""
    response = client.get("/")
    assert response.status_code == 200


def test_index_serves_root_mount_point(client) -> None:
    """The shell document carries ``<div id="root">`` — React's mount point."""
    response = client.get("/")
    assert b'<div id="root">' in response.content


def test_index_serves_same_shell_authed_and_unauthed(client, user_sub) -> None:
    """The server-side auth dispatch is gone: ``/`` returns the same shell
    document either way (the SPA decides welcome vs. app shell via ``/v1/me``).

    A regression that re-introduced the F-01 welcome/main template split would
    fail here — the two responses would diverge. The per-request CSRF token
    (rendered by ``{% csrf_token %}``) is normalized out before comparing so
    the assertion targets template choice, not token entropy.
    """
    anon_body = _CSRF_TOKEN_RE.sub(b"<csrf>", client.get("/").content)

    user = make_user(sub=user_sub, nick="alice")
    client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
    authed_body = _CSRF_TOKEN_RE.sub(b"<csrf>", client.get("/").content)

    assert anon_body == authed_body


def test_index_serves_vite_entry(client) -> None:
    """Phase 2: the shell document carries the django-vite entry tag for
    ``src/main.tsx``.

    In dev (DEBUG=True, the test default) django-vite emits a module script
    pointing at the Vite dev server; in prod it points at the hashed bundle
    under ``/static/assets/``. Both share the ``src/main.tsx`` path, which is
    the stable anchor across modes — assert on that, not on the host, so the
    test passes in either mode without a settings split.
    """
    response = client.get("/")
    assert b"src/main.tsx" in response.content
