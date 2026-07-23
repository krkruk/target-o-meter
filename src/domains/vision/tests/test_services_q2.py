"""``services.process_image`` + storage round-trip test (mock detector, no
q2 worker, no network).

Calls ``process_image`` synchronously on a freshly created ScoringJob;
verifies status flips to succeeded, 3 deliverable paths land on the job, and
``result`` JSON parses with the expected count.

Uses the versioned fixture at ``tests/fixtures/12.jpg`` — no dependency on
the unversioned ``resources/train/`` set.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.domains.vision.detectors.mock_detector import MockDetector
from src.domains.vision.models import ScoringJob
from src.domains.vision.pipeline.storage import ScoringStorage
from src.domains.vision.services import get_job, process_image


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
