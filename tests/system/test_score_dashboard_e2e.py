"""E2E (Playwright) test: the user-score-dashboard CRUD flow + UI, fully offline.

user-score-dashboard Phase 5 (the user-added E2E phase). Drives the REAL
assembled system through a browser: a live ``runserver`` subprocess (prod-shape
frontend bundle served via WhiteNoise, dev-auth-bypass auth, the MockDetector,
NO Google AI Studio key) is driven by Playwright to exercise the full read →
preview → modify → delete flow on a seeded accepted result.

Offline contract: ``GOOGLE_API_KEY`` is stripped by the conftest's
``_sanitized_env`` denylist (real credentials never inherit from the host), and
``VISION_DETECTOR=mock`` forces the MockDetector so no LLM call is ever made.
The seed creates the ScoringJob + AcceptedResult directly (the dashboard's CRUD
target); the E2E does NOT run the upload→detect pipeline (that path is covered
by test_spa_pipeline + test_accept_persist_blackbox). Here the point is the
dashboard's alteration scenario — applying CRUD on the already-processed values.

This is the blackbox + browser tier (V-Model top): the app is a black box, the
browser is a real user, and the only assertions are on what the user sees.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# NOTE: no ``pytest.mark.django_db`` here. pytest-playwright's ``page`` fixture
# runs inside an async event loop, which conflicts with pytest-django's
# synchronous DB setup (SynchronousOnlyOperation). This test drives a SEPARATE
# ``runserver`` subprocess whose throwaway SQLite DB lives under results/<run-id>/
# (pointed at by RAILWAY_VOLUME_MOUNT_PATH) — it does NOT touch the pytest test
# DB at all. Seeding goes through the app's own ``manage.py shell`` surface
# before the server boots. ``pytest.mark.dev`` keeps it in the default suite.
pytestmark = [pytest.mark.dev]


_REPO_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_DIST_MANIFEST = _REPO_ROOT / "src" / "frontend" / "dist" / "manifest.json"

# The dev-bypass sub. The middleware auto-authenticates as this user (DEBUG=True
# only). The seed pre-creates the User with has_set_nick=True so the SPA skips
# the nick prompt and lands on the dashboard.
_DEV_BYPASS_SUB = "auth0|e2e-score-dashboard"

# DEV-CONTAINER boot env: DEBUG=True (dev-bypass auth + the system check allows
# DEV_AUTH_BYPASS_SUB) + DJANGO_VITE_DEV_MODE=False (serve the baked bundle, no
# Vite dev server) + VISION_DETECTOR=mock (offline; no Google AI Studio call).
# GOOGLE_API_KEY is already stripped by conftest._sanitized_env — restated here
# for clarity (it stays unset, so the mock detector is the only path).
_BOOT_ENV = {
    "DEBUG": "True",
    "DJANGO_VITE_DEV_MODE": "False",
    "DEV_AUTH_BYPASS_SUB": _DEV_BYPASS_SUB,
    "VISION_DETECTOR": "mock",
    "ALLOWED_HOSTS": "*",
}

# Seed a ScoringJob (SUCCEEDED) + an AcceptedResult owned by the dev-bypass
# user. The seed runs as ``manage.py shell -c`` BEFORE the server boots (the
# server's module-level _dev_user cache then finds the row already present).
# 5 holes scoring 8 → score_average 8.0; the Modify step changes one to 10 →
# the average recompute is visible in the dashboard ( (8+8+8+8+10)/5 = 8.4 ).
_SEED_SCRIPT = (
    "import uuid, django; django.setup(); "
    "from src.domains.identity.models import User; "
    "from src.domains.vision.models import ScoringJob, AcceptedResult; "
    "u, _ = User.objects.get_or_create(sub=%r, defaults={'nick': 'e2e-shooter', 'has_set_nick': True}); "
    "u.has_set_nick = True; u.save(); "
    "job = ScoringJob.objects.create(user_uuid=u.id, status=ScoringJob.Status.SUCCEEDED, input_path='uploads/e2e.jpg'); "
    "ar = AcceptedResult.objects.create(user_uuid=u.id, source_job=job.id, target_type='air_pistol', holes=[{'x':%d,'y':%d,'score':%d,'confidence':1.0} for _ in range(5)], score_average=8.0); "
    "print('SEEDED', u.id, job.id, ar.id)"
) % (_DEV_BYPASS_SUB, 0, 0, 8)


pytestmark_skip_if_no_bundle = pytest.mark.skipif(
    not _FRONTEND_DIST_MANIFEST.exists(),
    reason="frontend bundle (src/frontend/dist/) missing — run `npm run build` in src/frontend/",
)


@pytest.mark.skipif(
    not _FRONTEND_DIST_MANIFEST.exists(),
    reason="frontend bundle (src/frontend/dist/) missing — run `npm run build` in src/frontend/",
)
def test_score_dashboard_crud_e2e(runserver_factory, page):
    """The full dashboard CRUD flow, driven through a real browser, offline.

    1. Boot the prod-shape server (baked bundle + dev-bypass + MockDetector).
    2. Seed one AcceptedResult (5 holes scoring 8 → average 8.0).
    3. Navigate to /scores → the row renders with the date + bolded 8.0.
    4. Preview → the proxy image + per-shot scores line render inline.
    5. Modify → change one hole 8 → 10 → the bolded average updates to 8.4.
    6. Delete → confirm → the row disappears.
    """
    server = runserver_factory(extra_env=_BOOT_ENV, seed_script=_SEED_SCRIPT)
    base = f"http://{server.addr}"

    # --- 1. The dashboard loads with the seeded row (8.0). -------------------
    page.goto(f"{base}/scores")
    # DEBUG: capture what rendered so a failure localizes the cause.
    try:
        page.wait_for_selector("text=8.0", timeout=15000)
    except Exception:
        server.run_dir.joinpath("e2e-debug.png").write_bytes(page.screenshot())
        server.run_dir.joinpath("e2e-debug.html").write_text(page.content())
        raise AssertionError(
            f"row '8.0' not visible within 15s.\n"
            f"  artifacts: {server.run_dir}/e2e-debug.png + e2e-debug.html\n"
            f"  server stderr (tail):\n{server.stderr[-2000:]}"
        ) from None
    # The three row action buttons are present (aria-labels disambiguate the
    # row Modify/Delete from the modal submit buttons).
    preview_btn = page.get_by_role("button", name="Preview score from")
    modify_row_btn = page.get_by_role("button", name="Modify score from")
    delete_row_btn = page.get_by_role("button", name="Delete score from")
    assert preview_btn.is_visible()
    assert modify_row_btn.is_visible()
    assert delete_row_btn.is_visible()

    # --- 2. Preview toggles the inline image + scores line. ------------------
    preview_btn.click()
    page.wait_for_selector('img[alt="Marked target"]', timeout=10000)
    assert page.is_visible("text=/Scores:/")
    # Close the preview (toggle) so it doesn't overlap the Modify click.
    preview_btn.click()

    # --- 3. Modify a hole (8 → 10) and assert the average recomputes. -------
    modify_row_btn.click()
    page.wait_for_selector("text=Holes", timeout=10000)
    # Change the first hole's score from 8 to 10 via its <select>. The modal's
    # hole selects carry id="modify-{i}" (scoped, not the page-size dropdown
    # which is the first <select> in document order on the dashboard).
    page.locator("#modify-0").select_option("10")
    # Submit the Modify (the modal's submit button — exact name "Modify").
    page.get_by_role("button", name="Modify", exact=True).click()
    # The modal closes; the row's bolded average is now 8.4
    # ((8+8+8+8+10)/5). Wait for it to appear.
    try:
        page.wait_for_selector("text=8.4", timeout=10000)
    except Exception:
        server.run_dir.joinpath("e2e-after-modify.png").write_bytes(page.screenshot())
        server.run_dir.joinpath("e2e-after-modify.html").write_text(page.content())
        raise AssertionError(
            f"'8.4' not visible after Modify.\n"
            f"  artifacts: {server.run_dir}/e2e-after-modify.png + .html\n"
            f"  server stderr (tail):\n{server.stderr[-1500:]}"
        ) from None

    # --- 4. Delete the row and assert it disappears. ------------------------
    # Re-resolve the row Delete button (the DOM re-rendered after the Modify).
    page.get_by_role("button", name="Delete score from").click()
    page.wait_for_selector("text=/Delete permanently/", timeout=10000)
    page.get_by_role("button", name="Delete permanently").click()
    # The row is gone — the empty state renders.
    page.wait_for_selector("text=/No scores yet/", timeout=10000)

    # --- 5. No server traceback throughout the flow. ------------------------
    server.assert_no_traceback()
