"""``services.process_image`` + storage round-trip test (mock detector, no
q2 worker, no network).

Calls ``process_image`` synchronously on a freshly created ScoringJob;
verifies status flips to succeeded, 3 deliverable paths land on the job, and
``result`` JSON parses with the expected count.

Uses the versioned fixture at ``tests/fixtures/12.jpg`` — no dependency on
the unversioned ``resources/train/`` set.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.conf import settings
from django.utils import timezone

from src.domains.vision.detectors.mock_detector import MockDetector
from src.domains.vision.models import ScoringJob
from src.domains.vision.pipeline.storage import ScoringStorage
from src.domains.vision.services import (
    get_job,
    process_image,
    reap_stuck_jobs,
    schedule_image_processing,
)


pytestmark = pytest.mark.django_db

FIXTURE_12 = Path(__file__).resolve().parent / "fixtures" / "12.jpg"


@pytest.fixture
def storage_with_upload(tmp_path: Path) -> tuple[ScoringStorage, str]:
    """Stage the versioned img 12 as an upload under a per-test bucket.

    Returns ``(storage, rel_input_path)`` so the test can create a ScoringJob
    pointing at the actual hashed path ``save_upload`` produces.
    """
    storage = ScoringStorage(location=tmp_path / "bucket")
    rel_input = storage.save_upload(FIXTURE_12.read_bytes(), "12.jpg")
    return storage, rel_input


def test_process_image_writes_deliverables_and_marks_succeeded(
    storage_with_upload: tuple[ScoringStorage, str],
) -> None:
    """End-to-end: create a ScoringJob, run process_image (mock detector),
    assert success + 3 deliverables stored."""
    storage, rel_input = storage_with_upload

    user_uuid = uuid4()
    job = ScoringJob.objects.create(
        user_uuid=user_uuid,
        status=ScoringJob.Status.QUEUED,
        input_path=rel_input,
        target_type="air_pistol",
        caliber_hint="9mm",
    )

    with patch(
        "src.domains.vision.services.GoogleAIStudioDetector",
        return_value=MockDetector(),
    ):
        with patch("src.domains.vision.services.ScoringStorage", lambda *a, **kw: storage):
            result = process_image(str(job.id))

    job.refresh_from_db()
    assert job.status == ScoringJob.Status.SUCCEEDED
    assert job.completed_at is not None
    assert job.llm_input_path
    assert job.marked_image_path
    assert job.result_json_path
    assert job.result["ok"] is True
    assert job.result["count"] == 5
    assert result["count"] == 5

    bucket = Path(storage._storage.location)
    # The 3 deliverable files exist on disk.
    assert (bucket / job.llm_input_path).exists()
    assert (bucket / job.marked_image_path).exists()
    assert (bucket / job.result_json_path).exists()


def test_get_job_enforces_owner_only(
    storage_with_upload: tuple[ScoringStorage, str],
) -> None:
    """``get_job`` raises ``PermissionError`` when ``user_uuid`` mismatches."""
    _, rel_input = storage_with_upload
    owner = uuid4()
    intruder = uuid4()
    job = ScoringJob.objects.create(
        user_uuid=owner,
        status=ScoringJob.Status.QUEUED,
        input_path=rel_input,
        target_type="air_pistol",
    )

    # Owner can read.
    dto = get_job(job.id, owner)
    assert dto.job_id == job.id

    # Intruder cannot.
    with pytest.raises(PermissionError, match="does not own"):
        get_job(job.id, intruder)


def test_process_image_marks_failed_on_exception(tmp_path: Path) -> None:
    """When the pipeline raises, ``process_image`` sets status=failed + error."""
    storage = ScoringStorage(location=tmp_path / "failed_bucket")
    storage.save_upload(b"not-an-image", "bogus.jpg")
    job = ScoringJob.objects.create(
        user_uuid=uuid4(),
        status=ScoringJob.Status.QUEUED,
        input_path="uploads/bogus.jpg",
        target_type="air_pistol",
    )

    with patch("src.domains.vision.services.GoogleAIStudioDetector", return_value=MockDetector()):
        with patch("src.domains.vision.services.ScoringStorage", lambda *a, **kw: storage):
            with pytest.raises(Exception):
                process_image(str(job.id))

    job.refresh_from_db()
    assert job.status == ScoringJob.Status.FAILED
    assert job.error
    assert job.completed_at is not None


def test_schedule_image_processing_rolls_back_if_enqueue_fails(
    storage_with_upload: tuple[ScoringStorage, str],
) -> None:
    """Atomicity contract: if django-q2's ``async_task`` raises after the row
    is created, the row MUST be rolled back — otherwise the BFF sees a job_id
    pointing at a queued row that no task will ever pick up.

    Guards the docstring claim in ``schedule_image_processing``. The atomicity
    is correct only while ``Q_CLUSTER['orm'] == 'default'`` (so the task row
    lands on the same DB connection); a separate test pins that invariant.
    """
    _, rel_input = storage_with_upload

    with patch(
        "django_q.tasks.async_task",
        side_effect=RuntimeError("q2 broker unreachable"),
    ):
        with pytest.raises(RuntimeError, match="q2 broker unreachable"):
            schedule_image_processing(
                user_uuid=uuid4(),
                input_path=rel_input,
                target_type="air_pistol",
                caliber_hint="9mm",
            )

    # Row must have rolled back — no orphan queued jobs left behind.
    assert ScoringJob.objects.filter(input_path=rel_input).count() == 0


def test_q_cluster_uses_orm_default_broker() -> None:
    """Pin the broker config that the atomicity contract above depends on.

    The atomicity of ``schedule_image_processing`` holds only while the q2
    broker writes its task row to the same DB connection as the ScoringJob
    table. A future switch to Redis/Disque would silently break the rollback
    test above; this guard makes the breakage loud.
    """
    cluster = settings.Q_CLUSTER
    assert cluster.get("orm") == "default", (
        "Q_CLUSTER.orm must be 'default' so the q2 task row lands in the same "
        "transaction as the ScoringJob row (AGENTS.md §2 SQLite-broker invariant)."
    )


def test_process_image_is_idempotent_on_terminal_state(
    storage_with_upload: tuple[ScoringStorage, str],
) -> None:
    """A second ``process_image`` call on an already-succeeded job MUST return
    early without re-running the pipeline or re-calling the LLM (q2 retry
    safety, F1 fix)."""
    storage, rel_input = storage_with_upload
    job = ScoringJob.objects.create(
        user_uuid=uuid4(),
        status=ScoringJob.Status.SUCCEEDED,
        input_path=rel_input,
        target_type="air_pistol",
        result={"ok": True, "prior": True},
    )

    detector_calls = []

    class _Sentinel(MockDetector):
        def detect(self, *a, **kw):  # type: ignore[override]
            detector_calls.append(1)
            return super().detect(*a, **kw)

    with patch("src.domains.vision.services.GoogleAIStudioDetector", return_value=_Sentinel()):
        with patch("src.domains.vision.services.ScoringStorage", lambda *a, **kw: storage):
            result = process_image(str(job.id))

    assert detector_calls == [], "detector must not run on terminal-state job"
    assert result == {"ok": True, "prior": True}
    job.refresh_from_db()
    assert job.status == ScoringJob.Status.SUCCEEDED


def test_reap_stuck_jobs_flips_stale_running_rows(
    storage_with_upload: tuple[ScoringStorage, str],
) -> None:
    """``reap_stuck_jobs`` transitions RUNNING rows older than the timeout to
    FAILED, closing the SIGKILL-while-running window (F2 fix)."""
    _, rel_input = storage_with_upload
    stale_time = timezone.now() - timedelta(seconds=3600)
    job = ScoringJob.objects.create(
        user_uuid=uuid4(),
        status=ScoringJob.Status.RUNNING,
        input_path=rel_input,
        target_type="air_pistol",
        started_at=stale_time,
    )

    n = reap_stuck_jobs(timeout_seconds=1200)
    assert n == 1

    job.refresh_from_db()
    assert job.status == ScoringJob.Status.FAILED
    assert "Reaped" in (job.error or "")
    assert job.completed_at is not None


def test_reap_stuck_jobs_leaves_fresh_running_rows_alone(
    storage_with_upload: tuple[ScoringStorage, str],
) -> None:
    """A RUNNING row whose started_at is within the timeout is NOT touched."""
    _, rel_input = storage_with_upload
    fresh = timezone.now() - timedelta(seconds=10)
    job = ScoringJob.objects.create(
        user_uuid=uuid4(),
        status=ScoringJob.Status.RUNNING,
        input_path=rel_input,
        target_type="air_pistol",
        started_at=fresh,
    )

    n = reap_stuck_jobs(timeout_seconds=1200)
    assert n == 0

    job.refresh_from_db()
    assert job.status == ScoringJob.Status.RUNNING
