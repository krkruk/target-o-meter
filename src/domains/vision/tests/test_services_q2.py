"""``services.process_image`` + storage round-trip test (mock detector, no
q2 worker, no network).

Calls ``process_image`` synchronously on a freshly created ScoringJob;
verifies status flips to succeeded, 3 deliverable paths land on the job, and
``result`` JSON parses with the expected count.
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


@pytest.fixture
def uploaded_input(tmp_path: Path) -> Path:
    """Stage a real train image as an "upload" via ScoringStorage."""
    source = Path("resources/train/12.jpg")
    storage = ScoringStorage(location=tmp_path / "bucket")
    storage.save_upload(source.read_bytes(), "12.jpg")
    # Return the bucket root so the test can resolve paths relative to it.
    return Path(storage._storage.location)


def test_process_image_writes_deliverables_and_marks_succeeded(
    uploaded_input: Path,
    tmp_path: Path,
    settings,
) -> None:
    """End-to-end: create a ScoringJob, run process_image (mock detector),
    assert success + 3 deliverables stored."""
    # Stage the input under the bucket so ScoringStorage can find it.
    bucket = uploaded_input
    storage = ScoringStorage(location=bucket)
    rel_input = "uploads/12.jpg"  # saved with hashed name; we overwrite below
    # Re-save under a known name (the test controls the input path).
    source_bytes = Path("resources/train/12.jpg").read_bytes()
    rel_input = storage.save_upload(source_bytes, "12.jpg")

    user_uuid = uuid4()
    job = ScoringJob.objects.create(
        user_uuid=user_uuid,
        status=ScoringJob.Status.QUEUED,
        input_path=rel_input,
        target_type="air_pistol",
        caliber_hint="9mm",
    )

    # Also need a marked-path source for AdaptiveFrameSizer — copy the marked
    # image alongside the upload so the geometry pass can read it. The pipeline
    # reads gt_marked_path only when explicitly passed; we skip it here.
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

    # The 3 deliverable files exist on disk.
    assert (bucket / job.llm_input_path).exists()
    assert (bucket / job.marked_image_path).exists()
    assert (bucket / job.result_json_path).exists()


def test_get_job_enforces_owner_only(uploaded_input: Path, settings) -> None:
    """``get_job`` raises ``PermissionError`` when ``user_uuid`` mismatches."""
    storage = ScoringStorage(location=uploaded_input)
    source_bytes = Path("resources/train/12.jpg").read_bytes()
    rel_input = storage.save_upload(source_bytes, "12.jpg")

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


def test_process_image_marks_failed_on_exception(settings) -> None:
    """When the pipeline raises, ``process_image`` sets status=failed + error."""
    storage = ScoringStorage(location=Path("/tmp/vision_test_failed_bucket"))
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
