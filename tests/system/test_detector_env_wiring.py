"""System tests: VISION_DETECTOR env wiring (Phase 3 manual tasks 3.4 / 3.5).

Blackbox verification that the Phase 3 wiring is live end-to-end — the manual
checks automated so they don't bit-rot. Two angles:

  - 3.4 — ``VISION_DETECTOR=mock`` routes ``process_image`` through
    ``DetectorFactory.build`` to ``MockDetector`` and yields the fixed 5-hole
    pattern. Driven as a blackbox: launch ``manage.py shell`` as a subprocess,
    migrate a throwaway DB, stage the fixture upload via the app's own
    ``ScoringStorage``, run ``process_image`` (which reads ``VISION_DETECTOR``),
    and assert on stdout + exit code. The test never imports app internals.
  - 3.5 — ``VISION_DETECTOR`` unset leaves prod-shape behavior: the app boots
    clean (the detector is constructed lazily at job-run time, never at boot).
    The unset→google mapping itself is unit-pinned
    (``test_factory_default_is_google``); this test pins boot-safety.

The fixture image is the versioned ``src/domains/vision/tests/fixtures/12.jpg``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANAGE_PY = _REPO_ROOT / "src" / "manage.py"
_FIXTURE_IMG = _REPO_ROOT / "src" / "domains" / "vision" / "tests" / "fixtures" / "12.jpg"

pytestmark = [pytest.mark.django_db, pytest.mark.dev]


# One Django shell script: migrate a throwaway DB, stage the fixture upload via
# the app's own default ScoringStorage (the same default process_image constructs
# internally, so the upload and the read-back agree), create a ScoringJob, run
# process_image (which reads VISION_DETECTOR), print a parseable result, then
# surgically remove only the upload + this job's deliverable dir it created.
# RAILWAY_VOLUME_MOUNT_PATH (set by the cli fixture) isolates the DB to the run
# dir; the FS storage default roots at BASE_DIR/scoring_storage (gitignored).
_PROCESS_SCRIPT = """
import os
import shutil
from django.core.management import call_command
call_command("migrate", verbosity=0)
from uuid import uuid4
from src.domains.vision.models import ScoringJob
from src.domains.vision.pipeline.storage import ScoringStorage
from src.domains.vision.services import process_image

img = os.environ["TOM_FIXTURE_IMG"]
storage = ScoringStorage()  # default dev path — matches process_image's internal storage
rel = storage.save_upload(open(img, "rb").read(), "12.jpg")
job = ScoringJob.objects.create(
    user_uuid=uuid4(),
    status=ScoringJob.Status.QUEUED,
    input_path=rel,
    target_type="air_pistol",
)
raised = None
try:
    process_image(str(job.id))
except Exception as exc:
    raised = exc  # surface after the RESULT line + cleanup
finally:
    job.refresh_from_db()
    ok = job.status == ScoringJob.Status.SUCCEEDED
    count = (job.result or {}).get("count", "n/a")
    print("RESULT ok=" + str(ok) + " count=" + str(count))
    # Self-clean only what THIS run created (the upload + this job's dir).
    try:
        storage._storage.delete(rel)
    except Exception:
        pass
    shutil.rmtree(
        os.path.join(storage._storage.location, "jobs", str(job.id)),
        ignore_errors=True,
    )
if raised:
    print("RAISED " + type(raised).__name__ + ": " + str(raised))
    raise raised
"""


def test_vision_detector_mock_runs_mockdetector_via_factory(cli) -> None:
    """Manual 3.4: VISION_DETECTOR=mock → process_image builds MockDetector via
    DetectorFactory.build and returns the fixed 5-hole pattern."""
    result = cli(
        [sys.executable, str(_MANAGE_PY), "shell", "-c", _PROCESS_SCRIPT],
        extra_env={
            "VISION_DETECTOR": "mock",
            "DJANGO_SETTINGS_MODULE": "src.target_o_meter.settings",
            "TOM_FIXTURE_IMG": str(_FIXTURE_IMG),
            "GOOGLE_API_KEY": "",  # mock needs no creds; blanked to keep it honest
        },
        cwd=_REPO_ROOT,
    )
    result.assert_success()
    assert b"RESULT ok=True count=5" in result.stdout, (
        f"expected the mock detector's 5-hole pattern via the factory; got:\n"
        f"stdout:\n{result.stdout.decode(errors='replace')}\n"
        f"stderr:\n{result.stderr.decode(errors='replace')}"
    )


def test_vision_detector_unset_boots_prod_shape(runserver) -> None:
    """Manual 3.5: VISION_DETECTOR unset leaves prod-shape behavior — the app
    boots clean (readiness OK, no traceback). ``.env`` does not set
    VISION_DETECTOR, so the inherited env is the unset/prod-shape state; the
    detector is only constructed inside ``process_image`` at job-run time,
    never at boot. The unset→google default is unit-pinned
    (``test_factory_default_is_google``)."""
    response = runserver.get("/v1/me")
    # 401 (unauthed) or 200 (dev bypass) are both fine — the point is the
    # server answered on the real stack with no traceback.
    assert response.status_code in (200, 401), response.text
    runserver.assert_no_traceback()
