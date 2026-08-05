"""E2E (Playwright): the FULL user path — upload → accept → dashboard modify-to-zeros → delete.

Sibling of ``test_score_dashboard_e2e.py``. Where that test seeds an
``AcceptedResult`` directly (to isolate the dashboard's CRUD), THIS test drives
the genuine end-to-end user journey through a real browser against a real,
fully-offline stack:

  1. Upload a real target image through the SPA wizard (/upload → /waiting).
  2. A sibling django-q2 worker runs ``process_image`` (MockDetector, no
     GOOGLE_API_KEY) until the job transitions queued → succeeded.
  3. Accept the result on /results → an ``AcceptedResult`` row is created.
  4. Open the /scores dashboard → the row renders with the accepted average.
  5. Modify every hole to 0 → assert each hole shows 0 AND the row's bolded
     average recomputes to 0.0.
  6. Delete the entry → assert the row disappears and the empty state renders.

Offline contract (same as the seeded sibling): ``GOOGLE_API_KEY`` is stripped by
``conftest._sanitized_env``; ``VISION_DETECTOR=mock`` forces the MockDetector so
no LLM call is ever made.

Worker contract: the system-test ``runserver_factory`` boots Django + migrate
+ collectstatic but NOT a qcluster — so this test spawns one itself, as a
sibling subprocess sharing the same clean run-dir DB + env (mirroring the
Playwright ``global-setup.ts`` stack). The worker is terminated in ``finally``.

This is the blackbox + browser tier (V-Model top): the app is a black box, the
browser is a real user, and the only assertions are on what the user sees.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect


# NOTE: no ``pytest.mark.django_db`` here. pytest-playwright's ``page`` fixture
# runs inside an async event loop, which conflicts with pytest-django's
# synchronous DB setup (SynchronousOnlyOperation). This test drives a SEPARATE
# ``runserver`` + ``qcluster`` pair whose throwaway SQLite DB lives under
# results/<run-id>/ (pointed at by RAILWAY_VOLUME_MOUNT_PATH) — it does NOT
# touch the pytest test DB at all. ``pytest.mark.dev`` keeps it in the default
# suite.
pytestmark = [pytest.mark.dev]


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANAGE_PY = _REPO_ROOT / "src" / "manage.py"
_FRONTEND_DIST_MANIFEST = _REPO_ROOT / "src" / "frontend" / "dist" / "manifest.json"
# The vision domain's versioned fixture image (same one scoring-flow.spec.ts
# uses for the acceptance upload flow).
_FIXTURE_IMG = (
    _REPO_ROOT / "src" / "domains" / "vision" / "tests" / "fixtures" / "12.jpg"
)

# The dev-bypass sub. The middleware auto-authenticates as this user (DEBUG=True
# only). The seed pre-creates the User with has_set_nick=True so the SPA skips
# the nick prompt and lands on the dashboard.
_DEV_BYPASS_SUB = "auth0|e2e-score-upload"

# DEV-CONTAINER boot env: DEBUG=True (dev-bypass auth + the system check allows
# DEV_AUTH_BYPASS_SUB) + DJANGO_VITE_DEV_MODE=False (serve the baked bundle, no
# Vite dev server) + VISION_DETECTOR=mock (offline; no Google AI Studio call).
# GOOGLE_API_KEY is already stripped by conftest._sanitized_env — restated here
# for clarity (it stays unset, so the mock detector is the only path).
# Pin the MockDetector's random pattern so the hole count is deterministic
# (matches the acceptance global-setup pin: seed 42, 5 holes).
_BOOT_ENV = {
    "DEBUG": "True",
    "DJANGO_VITE_DEV_MODE": "False",
    "DEV_AUTH_BYPASS_SUB": _DEV_BYPASS_SUB,
    "VISION_DETECTOR": "mock",
    "MOCK_DETECTOR_SEED": "42",
    "MOCK_DETECTOR_HOLE_COUNT": "5",
    "ALLOWED_HOSTS": "*",
}

# Pre-create the dev-bypass user with has_set_nick=True so the SPA skips the
# nick prompt and lands on the dashboard. No ScoringJob/AcceptedResult seed —
# the whole point of this test is that the user uploads one for real.
_SEED_SCRIPT = (
    "import django; django.setup(); "
    "from src.domains.identity.models import User; "
    "u, _ = User.objects.get_or_create(sub=%r, defaults={'nick': 'e2e-uploader'}); "
    "u.has_set_nick = True; u.save(); "
    "print('SEEDED', u.id)"
) % (_DEV_BYPASS_SUB,)

# How many holes the pinned MockDetector emits (kept in sync with
# MOCK_DETECTOR_HOLE_COUNT above). The Modify step iterates over exactly these.
_HOLE_COUNT = 5


pytestmark_skip_if_no_bundle = pytest.mark.skipif(
    not _FRONTEND_DIST_MANIFEST.exists(),
    reason="frontend bundle (src/frontend/dist/) missing — run `npm run build` in src/frontend/",
)


def _boot_worker(server, run_dir: Path) -> subprocess.Popen:
    """Spawn a sibling ``qcluster`` sharing the runserver's clean DB + env.

    Mirrors the Playwright global-setup stack: same ``base_env`` (so the worker
    sees the same ``RAILWAY_VOLUME_MOUNT_PATH`` DB, the same MockDetector pin,
    and the same sanitized env), ``--noreload`` keeps exactly one PID to kill.
    """
    stderr_file = run_dir / "qcluster.stderr"
    stderr_fp = stderr_file.open("w")
    proc = subprocess.Popen(
        [sys.executable, str(_MANAGE_PY), "qcluster"],
        cwd=str(_REPO_ROOT / "src"),
        env=server.base_env,
        stdout=stderr_fp,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    # Stash the fp + path on the handle so the test can read worker stderr for
    # post-mortem and so _stop_worker can close it.
    proc._stderr_fp = stderr_fp
    proc._stderr_path = stderr_file
    return proc


def _stop_worker(proc: subprocess.Popen) -> None:
    """Terminate the qcluster process group (the worker spawns children)."""
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    fp = getattr(proc, "_stderr_fp", None)
    if fp:
        fp.close()


@pytest.mark.skipif(
    not _FRONTEND_DIST_MANIFEST.exists(),
    reason="frontend bundle (src/frontend/dist/) missing — run `npm run build` in src/frontend/",
)
def test_score_dashboard_full_upload_modify_delete_e2e(runserver_factory, page):
    """The full user journey through a real browser, offline.

    1. Boot the prod-shape server (baked bundle + dev-bypass + MockDetector).
    2. Boot a sibling qcluster worker so the upload actually gets processed.
    3. Upload the fixture image through the SPA wizard → /waiting → /results.
    4. Accept the result → row appears on /scores with the accepted average.
    5. Modify every hole to 0 → assert all holes are 0 and the average is 0.0.
    6. Delete the entry → assert the row is gone and the empty state renders.
    """
    server = runserver_factory(extra_env=_BOOT_ENV, seed_script=_SEED_SCRIPT)
    base = f"http://{server.addr}"

    worker = _boot_worker(server, server.run_dir)
    try:
        # --- 1. Upload the fixture image through the SPA wizard. -------------
        page.goto(f"{base}/dashboard")
        page.get_by_role("button", name="Add photos").click()
        # Add-photos on desktop routes to /upload. Use the assertion-style URL
        # check (expect().to_have_url) everywhere rather than wait_for_url —
        # the SPA navigates client-side via React Router, and an event-style
        # wait_for_url issued right after the triggering action can miss a
        # navigation that already completed (the worker + MockDetector can be
        # fast enough to push /upload → /waiting → /results within one tick).
        expect(page).to_have_url(re.compile(r"/upload$"), timeout=15000)

        # Wizard step: pick caliber + distance, then advance to the file picker.
        page.get_by_role("combobox", name="Caliber").select_option("9x19mm")
        page.get_by_role("combobox", name="Distance").select_option("25")
        page.get_by_role("button", name="Next").click()

        # Upload the fixture image. The qcluster worker drives process_image to
        # succeeded; the waiting screen polls until /results transitions.
        file_input = page.get_by_label("Select a photo of your target")
        file_input.set_input_files(
            {
                "name": "target.jpg",
                "mimeType": "image/jpeg",
                "buffer": _FIXTURE_IMG.read_bytes(),
            }
        )
        # /waiting/:jobId polls until /results/:jobId. Wait directly for the
        # terminal URL (the transient /waiting/ can pass too fast to catch).
        # Generous timeout: the worker + pipeline take a few seconds.
        expect(page).to_have_url(re.compile(r"/results/"), timeout=60000)

        # The marked image + the MockDetector's pinned hole count rendered as
        # correction <select>s (id^="correct-") confirm a successful detection.
        page.wait_for_selector('img[alt="Marked target"]', timeout=10000)
        page.wait_for_selector('select[id^="correct-"]', timeout=10000)
        assert page.locator('select[id^="correct-"]').count() == _HOLE_COUNT

        # --- 2. Accept the result → AcceptedResult row is created. ----------
        page.get_by_role("button", name="Accept result").click()
        expect(page).to_have_url(re.compile(r"/dashboard$"), timeout=15000)

        # --- 3. The dashboard shows the row with the accepted average. ------
        page.goto(f"{base}/scores")
        # The row's bolded average is non-zero right after accept (5 holes from
        # the MockDetector, all > 0). Wait for the row's action buttons to
        # render before driving them (the DOM re-renders on refetch).
        page.wait_for_selector('button[aria-label^="Modify score from"]', timeout=15000)

        # --- 4. Modify every hole to 0 and assert the average recomputes. ---
        page.locator('button[aria-label^="Modify score from"]').first.click()
        page.wait_for_selector("text=Holes", timeout=10000)
        # Set each hole's <select> to "0" (id="modify-{i}", scoped inside the modal).
        for i in range(_HOLE_COUNT):
            page.locator(f"#modify-{i}").select_option("0")
        page.get_by_role("button", name="Modify", exact=True).click()

        # The modal closes; the row's bolded average is now 0.0. The substring
        # wait (matches the seeded sibling's "text=8.4" idiom) tolerates the
        # surrounding markup.
        try:
            page.wait_for_selector("text=0.0", timeout=10000)
        except Exception:
            server.run_dir.joinpath("e2e-after-modify.png").write_bytes(
                page.screenshot()
            )
            server.run_dir.joinpath("e2e-after-modify.html").write_text(page.content())
            raise AssertionError(
                f"'0.0' not visible after modifying all holes to 0.\n"
                f"  artifacts: {server.run_dir}/e2e-after-modify.png + .html\n"
                f"  server stderr (tail):\n{server.stderr[-1500:]}"
            ) from None

        # Re-open Modify to read the persisted holes back from getScore — the
        # authoritative check that every hole is 0 (not just the average).
        page.locator('button[aria-label^="Modify score from"]').first.click()
        page.wait_for_selector("text=Holes", timeout=10000)
        for i in range(_HOLE_COUNT):
            # The <select> reflects the persisted hole value (getScore snapshot).
            assert (
                page.locator(f"#modify-{i}").evaluate(
                    "el => el.options[el.selectedIndex].text"
                )
                == "0"
            ), f"hole {i} expected '0' after modify"
        # Close the modal (Cancel) so it doesn't overlap the Delete click.
        page.get_by_role("button", name="Cancel").click()

        # --- 5. Delete the entry → row disappears, empty state renders. -----
        # Re-resolve the row Delete button (the DOM re-rendered after the Modify).
        page.locator('button[aria-label^="Delete score from"]').first.click()
        page.wait_for_selector("text=/Delete permanently/", timeout=10000)
        page.get_by_role("button", name="Delete permanently").click()
        # The row is gone — the empty state renders.
        page.wait_for_selector("text=/No scores yet/", timeout=10000)

        # --- 6. No server traceback throughout the flow. --------------------
        server.assert_no_traceback()
    finally:
        _stop_worker(worker)
