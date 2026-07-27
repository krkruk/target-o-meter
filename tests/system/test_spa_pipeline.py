"""System test: ``/`` serves the SPA shell via the live ``runserver`` stack.

Phase 2 blackbox contract: django-vite is wired and the index view serves a
document carrying the React mount point + the vite entry. This is verified on
the real subprocess path (not the Django test client — that never exercises
WSGI / the actual template tag rendering through the serving stack) in both
modes:

  * DEV  (``DEBUG=True``):  the entry tag points at the Vite dev server.
  * PROD (``DEBUG=False``): the entry tag points at the hashed bundle under
    ``/static/assets/`` (read from ``dist/manifest.json``).
  * DEV-CONTAINER (``DEBUG=True`` + ``DJANGO_VITE_DEV_MODE=False``): the entry
    tag points at the hashed bundle too — the dev image bakes the bundle and
    ``docker-compose.dev.yml`` flips django-vite into manifest mode because it
    runs no Vite dev server on :5173. See ``test_dev_container_*`` below and
    S-02 impl-review F11.

The shared anchor across modes is ``src/main.tsx`` (dev-server URL carries it
verbatim; the prod manifest is keyed by it). All must also contain
``<div id="root">`` and produce no server traceback.

Phase 5.C extended the prod-mode test: the hashed bundle URL the served HTML
references must actually be **fetchable** (200 + JavaScript content-type) and
must carry the inlined SVG. The motivating bug: ``DEBUG=false make dev``
showed a blank page (the SPA — including the inlined target.svg — never
mounted) because ``collectstatic`` was never run and ``STORAGES`` had no
WhiteNoise entry, so requests for ``/static/assets/main-*.js`` 404'd in prod
mode. The original prod-mode test only asserted the HTML contained the script
tag; it never fetched the script, so the 404 shipped green. Mirrors the dev-
mode regression guard in ``test_vite_dev_server.py:test_served_script_url_is_
javascript_from_vite``.

Prerequisite for the prod case: ``src/frontend/dist/`` must exist (a built
manifest). The build is produced by ``npm run build`` and is part of the Phase
2 automated gate; this test skips the prod case with a clear reason when the
build is absent so a fresh checkout without a build does not produce a false
red.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


_REPO_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_DIST_MANIFEST = _REPO_ROOT / "src" / "frontend" / "dist" / "manifest.json"

# Prod-mode boot env. The SECRET_KEY is explicit (NOT inherited from the
# developer's ``.env``) because ``conftest._sanitized_env`` strips AUTH0_SECRET
# / SECRET_KEY to keep tests isolated from the host environment — a prod-shape
# boot (DEBUG=False) fails the E002 system check without a real key. The value
# is test-only (these boots never sign real sessions; they assert on the served
# HTML / bundle bytes). See S-02 impl-review F10.
_PROD_MODE_ENV = {
    "DEBUG": "False",
    "ALLOWED_HOSTS": "*",
    "SECRET_KEY": "test-only-prod-mode-boot-key-not-secret",
}

# Dev-container boot env. ``make dev-container`` runs the dev image with
# DEBUG=True (backend dev: autoreload, dev-bypass auth, USE_S3 against MinIO)
# but DJANGO_VITE_DEV_MODE=False so the frontend is served from the baked
# bundle (manifest mode) instead of an absent Vite dev server — there is no
# :5173 service in docker-compose.dev.yml. This is the third serving mode
# alongside native dev (DEBUG=True + Vite on :5173) and prod (DEBUG=False +
# manifest). See S-02 impl-review F11.
_DEV_CONTAINER_ENV = {
    "DEBUG": "True",
    "DJANGO_VITE_DEV_MODE": "False",
}


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
        extra_env=_PROD_MODE_ENV,
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


def test_prod_mode_hashed_bundle_is_served_as_javascript(runserver_factory) -> None:
    """PROD: the hashed bundle URL the shell references actually resolves to a
    JavaScript file (200 + ``text/javascript``).

    Phase 5.C regression guard for the blank-page bug. Before ``collectstatic``
    was wired into the boot and ``STORAGES`` had no WhiteNoise entry, this URL
    404'd in prod mode — the SPA never mounted, the inlined SVG vanished, and
    the served HTML still looked correct (script tag present, just pointing at a
    404). Mirrors the dev-mode guard at
    ``test_vite_dev_server.py:test_served_script_url_is_javascript_from_vite``.
    """
    if not _FRONTEND_DIST_MANIFEST.exists():
        pytest.skip(
            "src/frontend/dist/manifest.json absent — run `npm run build` in "
            "src/frontend/ before exercising the prod-mode bundle fetch."
        )

    server = runserver_factory(
        extra_env=_PROD_MODE_ENV,
    )
    html = server.get("/").text
    # Extract the hashed bundle URL django-vite emitted.
    m = re.search(r'<script[^>]+src="(/static/assets/main-[^"]+\.js)"', html)
    assert m, f"no hashed bundle script tag found in served HTML:\n{html}"
    script_path = m.group(1)

    # Fetch it exactly as the browser would.
    bundle_response = server.get(script_path)
    assert bundle_response.status_code == 200, (
        f"hashed bundle {script_path!r} returned "
        f"{bundle_response.status_code} — the SPA's JS is 404 in prod mode "
        f"(the blank-page bug). Is collectstatic wired + WhiteNoise storage set?"
    )
    content_type = bundle_response.headers.get("content-type", "")
    assert "javascript" in content_type, (
        f"hashed bundle {script_path!r} served as {content_type!r}, not "
        f"JavaScript. The browser would refuse to execute it → blank page."
    )
    server.assert_no_traceback()


def test_prod_mode_bundle_inlines_target_svg(runserver_factory) -> None:
    """PROD: the hashed JS bundle carries the inlined target.svg as a base64
    data URL, and NO separate ``/static/assets/target.svg`` is referenced.

    The SVG (``src/frontend/assets/target.svg``, ~1.9KB) is below Vite's default
    ``assetsInlineLimit`` (4096 bytes), so Vite inlines it as a
    ``data:image/svg+xml;base64,...`` string inside the JS bundle rather than
    emitting a separate hashed ``.svg`` file. This test pins BOTH halves of
    that delivery path:

      1. The bundle body carries the inlined data URL (the hero image ships).
      2. Neither the served HTML nor the bundle body references a separate
         ``assets/target.svg`` — which would 404 in prod (the exact symptom
         from the ``DEBUG=false make dev`` bug report: the browser requested
         ``/static/assets/target.svg`` and got 404 because no such file is
         emitted or collected).

    If a future ``assetsInlineLimit`` bump turns the SVG into a separate hashed
    file, half 1 fails (no inline) and half 2 names the new reference shape to
    add a fetch-assertion for.
    """
    if not _FRONTEND_DIST_MANIFEST.exists():
        pytest.skip(
            "src/frontend/dist/manifest.json absent — run `npm run build` in "
            "src/frontend/ before exercising the prod-mode SVG-inlined check."
        )

    server = runserver_factory(
        extra_env=_PROD_MODE_ENV,
    )
    html = server.get("/").text
    m = re.search(r'<script[^>]+src="(/static/assets/main-[^"]+\.js)"', html)
    assert m, f"no hashed bundle script tag found in served HTML:\n{html}"
    bundle_body = server.get(m.group(1)).text

    # Half 1: the SVG ships inlined as a base64 data URL.
    assert "data:image/svg+xml;base64," in bundle_body, (
        f"hashed bundle {m.group(1)!r} does not carry the inlined target.svg — "
        f"the welcome-page hero image would not render. (If Vite's "
        f"assetsInlineLimit was raised, the SVG is now a separate hashed file "
        f"and this assertion needs updating to fetch it.)"
    )

    # Half 2: no separate target.svg reference that would 404. The stale-bundle
    # bug (the original ``GET /static/assets/target.svg HTTP/1.1" 404``) is
    # caught here: a build that emits target.svg as a separate file leaves a
    # reference in the bundle that this asserts absent.
    assert "assets/target.svg" not in bundle_body, (
        f"hashed bundle {m.group(1)!r} references a separate "
        f"``assets/target.svg`` — that file is not emitted or collected, so the "
        f"browser would 404 on it (the DEBUG=false missing-SVG bug). Either the "
        f"bundle is stale (rebuild with `npm run build`) or Vite's "
        f"assetsInlineLimit changed the SVG's delivery path."
    )
    assert "assets/target.svg" not in html, (
        "served HTML references ``assets/target.svg`` directly — see bundle "
        "assertion above for the fix path."
    )
    server.assert_no_traceback()


# ---------------------------------------------------------------------------
# Dev-container mode (S-02 impl-review F11): DEBUG=True but no Vite dev server.
# ---------------------------------------------------------------------------
#
# ``make dev-container`` brings up docker-compose.dev.yml, which runs the dev
# image with DEBUG=True (backend dev) but DJANGO_VITE_DEV_MODE=False. The dev
# image bakes the frontend bundle (``npm ci && npm run build`` in the Dockerfile
# dev stage) and django-vite serves it from ``dist/manifest.json`` — no Vite
# process, no :5173, no HMR in-container (native ``make dev`` keeps HMR).
#
# The motivating bug: the dev compose has no Vite service, and ``dev_mode`` was
# bound directly to ``DEBUG``, so the dev container emitted
# ``<script src="http://localhost:5173/...">`` with nothing answering on :5173.
# The browser loaded the Django shell (fine) but the JS module import failed →
# ``#root`` stayed empty → a blank page. These tests pin the dev-container
# serving contract (manifest mode under DEBUG=True) so the regression can't
# recur. They run without Docker: they boot a plain runserver with the same
# env posture the dev compose sets.


def test_dev_container_mode_serves_hashed_bundle(runserver_factory) -> None:
    """DEV-CONTAINER: ``DEBUG=True`` + ``DJANGO_VITE_DEV_MODE=False`` serves the
    hashed bundle (not the absent :5173 dev server).

    Regression guard for the blank ``make dev-container`` page. The dev image
    bakes the bundle; the dev compose flips django-vite into manifest mode so
    the entry tag points at ``/static/assets/main-*.js`` instead of
    ``http://localhost:5173/...`` (which has no answerer in-container).
    """
    if not _FRONTEND_DIST_MANIFEST.exists():
        pytest.skip(
            "src/frontend/dist/manifest.json absent — run `npm run build` in "
            "src/frontend/ before exercising the dev-container bundle check."
        )

    server = runserver_factory(extra_env=_DEV_CONTAINER_ENV)
    html = server.get("/").text
    # The dev-server URL must NOT appear: nothing answers :5173 in-container.
    assert "localhost:5173" not in html, (
        "dev-container HTML references the Vite dev server (:5173), but "
        "docker-compose.dev.yml runs no Vite service — the browser would fail "
        "to import the entry module and #root stays empty (the blank page)."
    )
    # Manifest-mode signature: the hashed bundle under /static/assets/.
    assert "/static/assets/main-" in html
    assert ".js" in html
    assert '<div id="root">' in html
    server.assert_no_traceback()


def test_dev_container_mode_bundle_is_fetchable_javascript(runserver_factory) -> None:
    """DEV-CONTAINER: the hashed bundle the shell references resolves to a 200
    JavaScript file under DEBUG=True + manifest mode.

    Mirrors ``test_prod_mode_hashed_bundle_is_served_as_javascript`` for the
    dev-container posture. The bundle must be served via the DEBUG runserver's
    staticfiles finders (``src/frontend/dist`` is in STATICFILES_DIRS), so a
    200 here proves the dev image's baked bundle is reachable from the
    container's runserver.
    """
    if not _FRONTEND_DIST_MANIFEST.exists():
        pytest.skip(
            "src/frontend/dist/manifest.json absent — run `npm run build` in "
            "src/frontend/ before exercising the dev-container bundle fetch."
        )

    server = runserver_factory(extra_env=_DEV_CONTAINER_ENV)
    html = server.get("/").text
    m = re.search(r'<script[^>]+src="(/static/assets/main-[^"]+\.js)"', html)
    assert m, f"no hashed bundle script tag found in served HTML:\n{html}"
    script_path = m.group(1)

    bundle_response = server.get(script_path)
    assert bundle_response.status_code == 200, (
        f"hashed bundle {script_path!r} returned {bundle_response.status_code} "
        f"in dev-container mode — the SPA's JS is unreachable (the blank page). "
        f"Is the dev image baking the bundle into src/frontend/dist?"
    )
    content_type = bundle_response.headers.get("content-type", "")
    assert "javascript" in content_type, (
        f"hashed bundle {script_path!r} served as {content_type!r}, not "
        f"JavaScript in dev-container mode."
    )
    server.assert_no_traceback()
