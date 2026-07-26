"""System test: django-vite emits a dev-server URL Vite actually serves as JS.

The motivating bug (regression caught on real ``make dev`` smoke): django-vite
prepends Django's ``STATIC_URL`` (``"static/"``) to the dev-server URL it
emits, producing e.g. ``http://localhost:5173/static/src/main.tsx``. Vite does
NOT serve the module at that path — its dev server returns its SPA-fallback
(``index.html``) with ``Content-Type: text/html`` and HTTP 200. The browser
therefore receives an HTML document where it expected a JavaScript module,
the import fails silently, ``#root`` stays empty, and the page renders blank
even though the served HTML looks correct (root div + script tag present).

This test boots BOTH the Django runserver (DEBUG=True) and the Vite dev
server, fetches ``/`` from Django, extracts the script URL django-vite emits,
fetches it from Vite, and asserts Vite returns it as JavaScript — the
contract that makes the SPA actually mount in dev.
"""
from __future__ import annotations

import re
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


_REPO_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_DIR = _REPO_ROOT / "src" / "frontend"
_MANAGE_PY = _REPO_ROOT / "src" / "manage.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def vite_dev_server(request: pytest.FixtureRequest):
    """Boot the Vite dev server on a fixed port (:5173 — what settings.py
    configures django-vite to point at) and tear it down after the test.

    Skips the test if ``npm install`` hasn't run (no node_modules).
    """
    if not (_FRONTEND_DIR / "node_modules").exists():
        pytest.skip("src/frontend/node_modules absent — run `npm install` first")

    proc = subprocess.Popen(
        ["npm", "run", "dev", "--", "--port", "5173", "--strictPort"],
        cwd=str(_FRONTEND_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        # Readiness: poll the actual module path. Vite's base config affects
        # what `/` returns (302 redirect to base under base:'/static/'), so
        # polling `/` for 200 is fragile. The module path returns 200 + JS
        # content-type the moment Vite is ready, and 404/connection-refused
        # before — a clean readiness signal independent of base.
        readiness_url = "http://localhost:5173/static/src/main.tsx"
        deadline = time.monotonic() + 45.0
        ready = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            try:
                r = httpx.get(readiness_url, timeout=1.0)
                if r.status_code == 200 and "javascript" in r.headers.get("content-type", ""):
                    ready = True
                    break
            except httpx.HTTPError:
                time.sleep(0.3)
        if not ready:
            out = ""
            if proc.stdout:
                # Non-blocking read of whatever's buffered.
                import os
                try:
                    while True:
                        chunk = os.read(proc.stdout.fileno(), 4096)
                        if not chunk:
                            break
                        out += chunk.decode(errors="replace")
                        if len(out) > 4000:
                            break
                except OSError:
                    pass
            pytest.fail(f"Vite dev server did not become ready in 45s.\n{out}")
        yield "http://localhost:5173"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_served_script_url_is_javascript_from_vite(runserver, vite_dev_server):
    """The ``<script src=...>`` URL django-vite emits in DEBUG must be served
    by Vite as ``text/javascript`` — not its HTML SPA-fallback.

    Regression guard for the blank-page bug: if django-vite prepends
    ``/static/`` to the dev-server URL, Vite returns ``index.html`` with
    ``text/html``, the module import fails, and ``#root`` stays empty.
    """
    html = runserver.get("/").text
    # Extract the script src django-vite emitted.
    m = re.search(r'<script[^>]+src="(http://localhost:5173[^"]+)"', html)
    assert m, f"no vite script tag found in served HTML:\n{html}"
    script_url = m.group(1)

    # Fetch it from Vite exactly as the browser would.
    r = httpx.get(script_url, timeout=5.0)
    assert r.status_code == 200, f"Vite returned {r.status_code} for {script_url}"
    content_type = r.headers.get("content-type", "")
    assert "javascript" in content_type, (
        f"Vite served {script_url} as {content_type!r}, not JavaScript.\n"
        f"This is the blank-page bug: the browser receives HTML where it "
        f"expected a JS module, the import fails, and #root stays empty.\n"
        f"First line of body: {r.text.splitlines()[0] if r.text else '(empty)'}"
    )
    runserver.assert_no_traceback()


def test_dev_mode_proxy_serves_vite_assets_at_django_origin(
    runserver, vite_dev_server
):
    """DEV: a request for ``/static/<vite-path>`` at Django's origin (``:8000``)
    is proxied to Vite (``:5173``) so the browser never has to know which origin
    owns each asset.

    The motivating bug (``make dev`` at ``http://localhost:8000``): the browser
    loaded the SPA shell from Django (``:8000``) but the JS modules from Vite
    (``:5173``). Vite rewrites asset imports like
    ``import targetUrl from '../../assets/target.svg'`` into bare absolute paths
    (``/static/assets/target.svg?import``) and the asset's default-export URL is
    itself a bare ``/static/assets/target.svg`` path. Browsers resolve bare
    absolute paths against the **document's origin** (``:8000``), not the
    importing module's origin (``:5173``); both requests hit Django → 404 → the
    welcome-page hero SVG never rendered. Server log:

        GET /static/assets/target.svg HTTP/1.1" 404 1865

    Setting Vite's ``base`` to a full origin URL does NOT fix this — Vite's
    module-graph import rewriting always strips the origin and emits bare
    ``/static/...`` paths. The deterministic fix is Django-side: the custom
    ``runserver`` command wraps the WSGI app in a ``ViteProxyStaticFilesHandler``
    that proxies staticfiles misses to Vite. The browser resolves against
    ``:8000`` (as it does), Django proxies to Vite, Vite serves the asset, the
    image renders. In prod, WhiteNoise serves the collected bundle same-origin
    and the proxy never runs (the custom ``runserver`` isn't used by gunicorn).

    This test pins the proxy contract by hitting **Django's origin** (via the
    ``runserver`` client) for the plain SVG asset — the URL the ``<img src>``
    renders — and asserting Django returns it as ``image/svg+xml``. The asset
    lives only in Vite's source tree (not in Django's staticfiles finders), so a
    200 here proves the proxy is forwarding misses to Vite.
    """
    # The plain SVG fetch is what the <img src> renders — the load-bearing case.
    response = runserver.get("/static/assets/target.svg")
    assert response.status_code == 200, (
        f"Django returned {response.status_code} for /static/assets/target.svg — "
        f"the dev-mode Vite proxy is missing or not forwarding. The browser "
        f"would 404 → the welcome-page hero SVG would not render (the ``make "
        f"dev`` missing-image bug)."
    )
    content_type = response.headers.get("content-type", "")
    assert "image/svg+xml" in content_type, (
        f"Django proxied /static/assets/target.svg but the response is "
        f"{content_type!r}, not image/svg+xml. The <img src> would not render."
    )
    # Sanity: the body is actually the SVG (carries the <svg> root element).
    assert "<svg" in response.text, (
        "proxied response body does not start with <svg — wrong asset served."
    )
    runserver.assert_no_traceback()
