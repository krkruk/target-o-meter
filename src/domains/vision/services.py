"""Public seam of the vision domain — what the BFF calls.

Per AGENTS.md §6.2 — BFF wraps ``schedule_image_processing`` in
``transaction.atomic()``. Two entry points:

  - ``schedule_image_processing(...)`` — synchronous enqueue: creates a
    ``ScoringJob(status="queued")`` and enqueues ``process_image`` on
    django-q2. Returns ``job.id``.
  - ``process_image(job_id)`` — the q2 task body. Loads the ``ScoringJob``,
    builds the detector from config (default ``GoogleAIStudioDetector``),
    runs ``PipelineRunner.run(...)`` writing deliverables via
    ``ScoringStorage``, stores the result JSON + paths on the job, sets
    ``status="succeeded"`` (or ``failed`` + error on exception).
  - ``get_job(job_id, user_uuid)`` — read accessor enforcing owner-only
    access (AGENTS.md §2 roles).

This module MUST NOT import django-ninja or handle HTTP (AGENTS.md §5).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from src.domains.vision.dtos import (
    DetectedHoleDTO,
    ScoringJobDTO,
    ScoringResultDTO,
)
from src.domains.vision.detectors.detection_result import DetectionResult
from src.domains.vision.detectors.google_ai_studio_detector import (
    GoogleAIStudioDetector,
)
from src.domains.vision.models import ScoringJob
from src.domains.vision.pipeline.pipeline_runner import PipelineRunner
from src.domains.vision.pipeline.storage import ScoringStorage
from src.domains.vision.ports import TargetType


logger = logging.getLogger(__name__)


def _sanitize_nan_inf(obj):
    """Recursively replace NaN / ±Infinity floats with ``None`` so SQLite's
    strict JSON_VALID accepts the serialized result dict.

    The pipeline emits ``final_cost=float("nan")`` on the skip-defense path
    (research § "Final per-image results": images 1/6/10/12/19/21/29). Those
    values are diagnostics; nulling them in the DB row is fine — the
    full-precision values still land in the ``_result.json`` deliverable.
    """
    import math
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_nan_inf(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_nan_inf(v) for v in obj]
    return obj


def schedule_image_processing(
    *,
    user_uuid: UUID,
    input_path: str,
    target_type: TargetType = "air_pistol",
    caliber_hint: Optional[str] = None,
) -> str:
    """Create a ``ScoringJob(status="queued")`` and enqueue ``process_image``
    on django-q2. Returns ``job.id`` (the cross-domain safe key per AGENTS.md §5).

    Atomic: the job row + the q2 enqueue land together (or neither does).
    """
    with transaction.atomic():
        job = ScoringJob.objects.create(
            user_uuid=user_uuid,
            status=ScoringJob.Status.QUEUED,
            input_path=input_path,
            target_type=target_type,
            caliber_hint=caliber_hint,
        )
        # Lazy import so the module loads cleanly even if django_q isn't in
        # INSTALLED_APPS yet (the BFF orchestration change wires q2 + config).
        from django_q.tasks import async_task
        async_task(
            "src.domains.vision.services.process_image",
            str(job.id),
        )
    return str(job.id)


def process_image(job_id: str | UUID) -> dict:
    """The q2 task body. Synchronous: runs the pipeline, stores results on the
    job row + writes the 3 deliverables via ``ScoringStorage``.

    Returns the result dict (also stored as ``job.result``). On exception,
    sets ``status="failed"`` + ``error`` trace.
    """
    job = ScoringJob.objects.get(id=job_id)

    # Mark running.
    job.status = ScoringJob.Status.RUNNING
    job.save(update_fields=["status", "updated_at"])

    try:
        # Build the detector from config (default Google; future: env switch).
        detector = GoogleAIStudioDetector()
        runner = PipelineRunner(detector)
        storage = ScoringStorage()

        # Materialize the input as a temp file for PipelineRunner (which takes
        # a path). The upload is already on disk; resolve to absolute path.
        input_abspath = storage.absolute_path(job.input_path)

        # Deliverables go into a per-job bucket; PipelineRunner writes via
        # its own out_dir, then we record the relative paths on the job.
        job_uuid = job.id
        out_dir = storage.deliverable_dir(job_uuid)
        out_dir.mkdir(parents=True, exist_ok=True)

        stem = Path(job.input_path).stem
        result_dict = runner.run(
            input_abspath,
            target_type=job.target_type,
            caliber_hint=job.caliber_hint,
            out_dir=out_dir,
        )

        # Move/capture the 3 deliverable paths (relative to storage root).
        # PipelineRunner wrote them as <stem>_llm_input.png etc.
        storage_root = Path(storage.absolute_path(".")).resolve()
        def _rel(p: Path) -> str:
            try:
                return str(p.resolve().relative_to(storage_root))
            except ValueError:
                return str(p)

        llm_path = out_dir / f"{stem}_llm_input.png"
        marked_path = out_dir / f"{stem}_marked.png"
        result_json_path = out_dir / f"{stem}_result.json"

        # Normalize numpy types AND NaN/Infinity out — SQLite's JSON_VALID is
        # strict (Python's ``json.dumps`` emits bare ``NaN`` / ``Infinity``
        # tokens which SQLite rejects). The on-disk _result.json file is fine
        # because consumers (browsers, jq) tolerate them; the DB column is not.
        from src.domains.vision.pipeline.pipeline_runner import _json_default
        job.result = json.loads(
            json.dumps(
                _sanitize_nan_inf(result_dict),
                default=_json_default,
                allow_nan=False,
            )
        )
        job.llm_input_path = _rel(llm_path)
        job.marked_image_path = _rel(marked_path)
        job.result_json_path = _rel(result_json_path)
        job.status = ScoringJob.Status.SUCCEEDED
        job.completed_at = timezone.now()
        job.save(update_fields=[
            "status", "result", "llm_input_path", "marked_image_path",
            "result_json_path", "completed_at", "updated_at",
        ])

        return result_dict

    except Exception as exc:
        logger.exception("process_image failed for job %s", job_id)
        job.status = ScoringJob.Status.FAILED
        job.error = f"{type(exc).__name__}: {exc}"
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "error", "completed_at", "updated_at"])
        raise


def get_job(job_id: str | UUID, user_uuid: UUID) -> ScoringJobDTO:
    """Read accessor enforcing owner-only access.

    Raises ``PermissionError`` if ``user_uuid`` does not match the job's
    ``user_uuid`` (AGENTS.md §2 roles). Returns a ``ScoringJobDTO``.
    """
    job = ScoringJob.objects.get(id=job_id)
    if job.user_uuid != user_uuid:
        raise PermissionError(
            f"user_uuid {user_uuid} does not own ScoringJob {job_id}"
        )
    return _job_to_dto(job)


def _to_result_dto(result: DetectionResult) -> ScoringResultDTO:
    """Map internal ``DetectionResult`` → ``ScoringResultDTO``.

    DTOs cross boundaries (AGENTS.md §5); the dataclass stays internal to the
    domain. Centralized here so the BFF gets one mapping surface.
    """
    return ScoringResultDTO(
        holes=[
            DetectedHoleDTO(
                x=h.x, y=h.y, score=h.score,
                confidence=h.confidence, caliber=h.caliber,
            )
            for h in result.holes
        ],
        target_type=result.target_type,
        notes=result.notes,
        detector_name=result.detector_name,
    )


def _job_to_dto(job: ScoringJob) -> ScoringJobDTO:
    """Map ``ScoringJob`` ORM row → ``ScoringJobDTO``."""
    result_dto: Optional[ScoringResultDTO] = None
    if job.result:
        # job.result is the result_dict shape (not DetectionResult); reconstruct
        # via the dict shape PipelineRunner wrote. For now, expose the stored
        # dict's detector + holes if present.
        result_dict = job.result
        if isinstance(result_dict, dict) and result_dict.get("ok"):
            holes_list = result_dict.get("holes", [])
            result_dto = ScoringResultDTO(
                holes=[
                    DetectedHoleDTO(
                        x=int(h.get("x", 0)),
                        y=int(h.get("y", 0)),
                        score=int(h.get("score", 0)),
                        confidence=float(h.get("confidence", 1.0)),
                        caliber=h.get("caliber"),
                    )
                    for h in holes_list
                ],
                target_type=result_dict.get("target_type", "air_pistol"),
                notes=result_dict.get("notes"),
                detector_name=result_dict.get("detector", ""),
            )

    return ScoringJobDTO(
        job_id=job.id,
        status=job.status,
        target_type=job.target_type,
        caliber_hint=job.caliber_hint,
        result=result_dto,
        error=job.error,
        created_at=job.created_at.isoformat() if job.created_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
    )
