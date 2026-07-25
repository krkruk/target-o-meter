"""System test: ``/`` serves the SPA shell via the live ``runserver`` stack.

Phase 2 blackbox contract: django-vite is wired and the index view serves a
document carrying the React mount point + the vite entry. This is verified on
the real subprocess path (not the Django test client — that never exercises
WSGI / the actual template tag rendering through the serving stack) in both
modes:

  * DEV  (``DEBUG=True``):  the entry tag points at the Vite dev server.
  * PROD (``DEBUG=False``): the entry tag points at the hashed bundle under
    ``/static/assets/`` (read from ``dist/manifest.json``).

The shared anchor across modes is ``src/main.tsx`` (dev-server URL carries it
verbatim; the prod manifest is keyed by it). Both must also contain
``<div id="root">`` and produce no server traceback.

Prerequisite for the prod case: ``src/frontend/dist/`` must exist (a built
manifest). The build is produced by ``npm run build`` and is part of the Phase
2 automated gate; this test skips the prod case with a clear reason when the
build is absent so a fresh checkout without a build does not produce a false
red.
"""
from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


_REPO_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_DIST_MANIFEST = _REPO_ROOT / "src" / "frontend" / "dist" / "manifest.json"


def test_index_serves_spa_shell_dev_mode(runserver) -> None:
    """DEV: ``/`` serves the shell with the vite entry on the dev server.

    The default ``runserver`` boot inherits ``DEBUG=True`` (settings default),
    so django-vite emits a module script pointing at ``localhost:5173``.
    """
    response = runserver.get("/")
    assert response.status_code == 200, response.text
    body = response.text
    assert '<div id="root">' in body
    # The entry anchor — stable across dev/prod.
    assert "src/main.tsx" in body
    # Dev-mode signature: the script src points at the Vite dev server.
    assert "localhost:5173" in body
    runserver.assert_no_traceback()


def test_index_serves_spa_shell_prod_mode(runserver_factory) -> None:
    """PROD: ``/`` serves the shell with the hashed bundle from the manifest.

    Boots with ``DEBUG=False`` + ``ALLOWED_HOSTS=*`` so django-vite reads
    ``dist/manifest.json`` and emits a hashed ``/static/assets/main-*.js`` URL.
    Requires a built ``dist/``; skipped (not failed) when absent.
    """
    if not _FRONTEND_DIST_MANIFEST.exists():
        pytest.skip(
            "src/frontend/dist/manifest.json absent — run `npm run build` in "
            "src/frontend/ before exercising the prod-mode shell."
        )

    server = runserver_factory(
        extra_env={"DEBUG": "False", "ALLOWED_HOSTS": "*"},
    )
    response = server.get("/")
    assert response.status_code == 200, response.text
    body = response.text
    assert '<div id="root">' in body
    # Prod-mode signature: the hashed bundle under /static/assets/.
    assert "/static/assets/main-" in body
    assert ".js" in body
    # The dev-server URL must NOT leak into prod mode.
    assert "localhost:5173" not in body
    server.assert_no_traceback()
