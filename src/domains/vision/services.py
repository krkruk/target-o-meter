"""Public seam of the vision domain — what the BFF calls.

Per AGENTS.md §6.2 — BFF wraps ``schedule_image_processing`` in
``transaction.atomic()``. Two entry points:

  - ``schedule_image_processing(...)`` — synchronous enqueue: creates a
    ``ScoringJob(status="queued")`` and enqueues ``process_image`` on
    django-q2. Returns ``job.id``.
  - ``process_image(job_id)`` — the q2 task body. Loads the ``ScoringJob``,
    builds the detector via ``DetectorFactory.build(VISION_DETECTOR)`` (env-
    driven; defaults to ``"google"`` so prod behavior is unchanged, dev flips
    to ``"mock"`` with no API key),
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
import os
import tempfile
from pathlib import Path
from typing import Optional
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from src.domains.vision.dtos import (
    AcceptedResultDTO,
    AggregationDTO,
    DailyAverageDTO,
    DetectedHoleDTO,
    HeroStatsDTO,
    ResultSummaryDTO,
    ScoringJobDTO,
    ScoringResultDTO,
)
from src.domains.vision.detectors.detection_result import DetectionResult
from src.domains.vision.detectors.factory import DetectorFactory
from src.domains.vision.models import AcceptedResult, ScoringJob
from src.domains.vision.pipeline.pipeline_runner import PipelineRunner
from src.domains.vision.pipeline.storage import ScoringStorage
from src.domains.vision.ports import TargetType


logger = logging.getLogger(__name__)


class StateError(Exception):
    """Raised when a domain operation is attempted on a row in the wrong state.

    Mirrors the existing ``PermissionError`` convention: a typed domain
    exception the BFF translates to an HTTP status (409 Conflict for
    ``StateError``). Used by ``accept_job`` to refuse accepting a
    ``ScoringJob`` that is not yet ``SUCCEEDED`` (FR-010 — only a completed
    detection can be accepted).
    """


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
    distance: Optional[int] = None,
    weapon_type: Optional[str] = None,
) -> str:
    """Create a ``ScoringJob(status="queued")`` and enqueue ``process_image``
    on django-q2. Returns ``job.id`` (the cross-domain safe key per AGENTS.md §5).

    Atomic: the job row + the q2 enqueue land together (or neither does).

    ``distance`` + ``weapon_type`` are S-03 FR-009 confirmation params persisted
    on the row (and snapshotted onto ``AcceptedResult`` at accept). They are
    metadata only — NOT forwarded to the detector; ``process_image`` calls
    ``PipelineRunner.run`` with ``target_type`` + ``caliber_hint`` alone (the
    detector ignores distance/weapon_type today).
    """
    with transaction.atomic():
        job = ScoringJob.objects.create(
            user_uuid=user_uuid,
            status=ScoringJob.Status.QUEUED,
            input_path=input_path,
            target_type=target_type,
            caliber_hint=caliber_hint,
            distance=distance,
            weapon_type=weapon_type,
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

    Idempotency: if the job is already in a terminal state (SUCCEEDED or
    FAILED), returns immediately without re-running — q2 retries (configured
    at settings.Q_CLUSTER['retry']) will not double-bill the LLM API or
    overwrite prior deliverables. The claim is a short atomic block so the
    ~30s pipeline does not hold a row lock (SQLite uses database-level
    locking under WAL).
    """
    with transaction.atomic():
        job = ScoringJob.objects.select_for_update().get(id=job_id)
        if job.status in (
            ScoringJob.Status.SUCCEEDED,
            ScoringJob.Status.FAILED,
        ):
            logger.warning(
                "process_image called for job %s already in terminal %s; "
                "skipping (q2 retry).",
                job_id, job.status,
            )
            return job.result or {}
        job.status = ScoringJob.Status.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at", "updated_at"])

    try:
        # Build the detector via the factory, env-driven so dev can flip to
        # MockDetector (VISION_DETECTOR=mock) and S-03 to Ollama without code
        # changes. Default "google" keeps prod behavior unchanged.
        detector_name = os.environ.get("VISION_DETECTOR", "google")
        detector = DetectorFactory.build(detector_name)
        runner = PipelineRunner(detector)
        storage = ScoringStorage()

        # S-03 Phase 4: ``cv2.imread`` cannot read an S3 key, and S3 has no
        # directories. The byte-oriented surface (``read_upload_bytes`` +
        # ``write_deliverable_bytes``) works under both backends: download the
        # upload to a tempfile (so the path-based pipeline + cv2 can read it),
        # write deliverables to a temp dir, then upload each one back. The
        # detector + PipelineRunner internals are untouched — only the storage
        # surface + these call sites change.
        job_uuid = job.id
        orig_stem = Path(job.input_path).stem
        orig_suffix = Path(job.input_path).suffix
        upload_bytes = storage.read_upload_bytes(job.input_path)
        with tempfile.NamedTemporaryFile(
            prefix=f"{orig_stem}_", suffix=orig_suffix, delete=False,
        ) as tf:
            tf.write(upload_bytes)
            input_abspath = Path(tf.name)
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                out_dir = Path(tmp_dir)
                result_dict = runner.run(
                    input_abspath,
                    target_type=job.target_type,
                    caliber_hint=job.caliber_hint,
                    out_dir=out_dir,
                )
                # PipelineRunner names deliverables after ``image_path.stem``
                # (the tempfile's stem here). Derive the names from the actual
                # temp path so they match what the runner wrote, then upload
                # each back; the returned keys are stored on the job (replacing
                # the old ``_rel(out_dir / name)`` values).
                stem = input_abspath.stem
                job.llm_input_path = storage.write_deliverable_bytes(
                    job_uuid, f"{stem}_llm_input.png",
                    (out_dir / f"{stem}_llm_input.png").read_bytes(),
                )
                job.marked_image_path = storage.write_deliverable_bytes(
                    job_uuid, f"{stem}_marked.png",
                    (out_dir / f"{stem}_marked.png").read_bytes(),
                )
                job.result_json_path = storage.write_deliverable_bytes(
                    job_uuid, f"{stem}_result.json",
                    (out_dir / f"{stem}_result.json").read_bytes(),
                )
        finally:
            input_abspath.unlink(missing_ok=True)

        # Normalize numpy types AND NaN/Infinity out — SQLite's JSON_VALID is
        # strict (Python's ``json.dumps`` emits bare ``NaN`` / ``Infinity``
        # tokens which SQLite rejects). The on-disk _result.json file is fine
        # because consumers (browsers, jq) tolerate them; the DB column is not.
        from src.domains.vision.pipeline.pipeline_runner import json_default
        job.result = json.loads(
            json.dumps(
                _sanitize_nan_inf(result_dict),
                default=json_default,
                allow_nan=False,
            )
        )
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


# Stuck-job detection — rows older than this while still RUNNING are assumed
# orphaned by a SIGKILLed worker (OOM, deploy, host reboot) and reaped.
STUCK_RUNNING_TIMEOUT_SECONDS = 1200  # 2× settings.Q_CLUSTER['timeout'] (600s); generous headroom over the ~30s pipeline + q2 timeout


def reap_stuck_jobs(timeout_seconds: int = STUCK_RUNNING_TIMEOUT_SECONDS) -> int:
    """Flip stale ``RUNNING`` rows back to ``FAILED``.

    A worker that is SIGKILL'd between setting ``status=RUNNING`` and writing
    a terminal state strands the row. This helper (intended to be called by a
    scheduled q2 task or the BFF-on-GET) marks such rows FAILED so callers
    see a terminal state instead of waiting forever.

    Returns the count of reaped rows. Rows without ``started_at`` (queued
    before the field existed, or never picked up) are not touched — those are
    q2's responsibility, not ours.
    """
    cutoff = timezone.now() - timezone.timedelta(seconds=timeout_seconds)
    with transaction.atomic():
        stale = list(
            ScoringJob.objects.select_for_update().filter(
                status=ScoringJob.Status.RUNNING,
                started_at__lt=cutoff,
            )
        )
        for job in stale:
            job.status = ScoringJob.Status.FAILED
            job.error = (
                f"Reaped: started_at {job.started_at.isoformat()} exceeded "
                f"STUCK_RUNNING_TIMEOUT_SECONDS={timeout_seconds}"
            )
            job.completed_at = timezone.now()
            job.save(update_fields=[
                "status", "error", "completed_at", "updated_at",
            ])
            logger.warning("Reaped stuck ScoringJob %s", job.id)
    return len(stale)


def get_job(job_id: str | UUID, user_uuid: UUID) -> ScoringJobDTO:
    """Read accessor enforcing owner-only access.

    Raises ``PermissionError`` if ``user_uuid`` does not match the job's
    ``user_uuid`` (AGENTS.md §2 roles) OR if the row is absent — both cases
    look identical to the caller so an ID-prober can't distinguish "exists,
    not mine" from "doesn't exist". The BFF should still map to 404.
    Returns a ``ScoringJobDTO``.
    """
    try:
        job = ScoringJob.objects.get(id=job_id)
    except ScoringJob.DoesNotExist as exc:
        raise PermissionError(f"ScoringJob {job_id} not visible to user_uuid {user_uuid}") from exc
    if job.user_uuid != user_uuid:
        raise PermissionError(
            f"user_uuid {user_uuid} does not own ScoringJob {job_id}"
        )
    return _job_to_dto(job)


def accept_job(
    *,
    job_id: str | UUID,
    user_uuid: UUID,
    target_type: TargetType,
    caliber_hint: Optional[str],
    distance: Optional[int],
    weapon_type: Optional[str],
    holes: list[DetectedHoleDTO],
) -> tuple[AcceptedResultDTO, bool]:
    """Accept a succeeded ``ScoringJob``'s detection result, persisting an
    immutable ``AcceptedResult`` snapshotting the confirmed params + corrected
    holes + computed score (S-03 Phase 2, FR-010).

    Guarantees (mirroring ``get_job``):

      - **Ownership** — raises ``PermissionError`` if the job is missing or
        owned by another user (both look identical to the caller → 404).
      - **State** — raises ``StateError`` if ``job.status != SUCCEEDED``; a
        queued/running/failed job cannot be accepted (the route maps to 409).
      - **Idempotency + race-safety** — DB-enforced via
        ``unique_together = ("source_job", "user_uuid")``. A concurrent accept
        for the same job raises ``IntegrityError``; this is caught and the
        existing ``AcceptedResult`` is re-fetched + returned (the 200 path).
        This is the canonical insert-or-return-existing idiom — safer than a
        check-then-create sequence, which under SQLite's default isolation lets
        two transactions both pass the check and both insert.

    Returns ``(dto, created)`` — ``created`` is ``True`` when this call inserted
    a new row (HTTP 201), ``False`` when an existing row was returned (HTTP
    200, the idempotent / race-loser path). Returning the flag directly avoids
    a fragile timestamp-based 201/200 split in the route.

    The ``ScoringJob`` row is NOT deleted (FR-011: reject is the absence of an
    ``AcceptedResult``; the CV row stays as an audit record).

    ``score_average`` is the mean of the holes' scores (the value hero-stats
    + the daily-average chart read).
    """
    with transaction.atomic():
        try:
            job = ScoringJob.objects.get(id=job_id)
        except ScoringJob.DoesNotExist as exc:
            raise PermissionError(
                f"ScoringJob {job_id} not visible to user_uuid {user_uuid}"
            ) from exc
        if job.user_uuid != user_uuid:
            raise PermissionError(
                f"user_uuid {user_uuid} does not own ScoringJob {job_id}"
            )
        if job.status != ScoringJob.Status.SUCCEEDED:
            raise StateError(
                f"ScoringJob {job_id} is {job.status}, not succeeded — cannot accept"
            )

        holes_payload = [
            {"x": h.x, "y": h.y, "score": h.score,
             "confidence": h.confidence, "caliber": h.caliber}
            for h in holes
        ]
        score_average = sum(h.score for h in holes) / len(holes)

        # The create is attempted inside a SAVEPOINT (not the outer atomic
        # block) so that on the unique_together IntegrityError we can roll
        # back to the savepoint and still issue the re-fetch query. Catching
        # an IntegrityError inside the SAME atomic block without a savepoint
        # poisons the whole transaction (Django raises
        # TransactionManagementError on the next query) — the savepoint is the
        # standard fix for insert-or-return-existing under a real constraint.
        created = True
        try:
            with transaction.atomic():
                ar = AcceptedResult.objects.create(
                    user_uuid=user_uuid,
                    source_job=job.id,
                    target_type=target_type,
                    caliber_hint=caliber_hint,
                    distance=distance,
                    weapon_type=weapon_type,
                    holes=holes_payload,
                    score_average=score_average,
                )
        except IntegrityError:
            # A concurrent accept won the unique_together race — the savepoint
            # rolled back, the outer transaction is still valid, re-fetch the
            # existing row (200 idempotent path). The IntegrityError must be
            # caught so it never surfaces as a 500.
            ar = AcceptedResult.objects.get(source_job=job.id, user_uuid=user_uuid)
            created = False
        return _accepted_result_to_dto(ar), created


def _accepted_result_to_dto(ar: AcceptedResult) -> AcceptedResultDTO:
    """Map an ``AcceptedResult`` ORM row → ``AcceptedResultDTO``."""
    return AcceptedResultDTO(
        result_id=ar.id,
        source_job=ar.source_job,
        target_type=ar.target_type,
        caliber_hint=ar.caliber_hint,
        distance=ar.distance,
        weapon_type=ar.weapon_type,
        holes=[
            DetectedHoleDTO(
                x=h["x"], y=h["y"], score=h["score"],
                confidence=h.get("confidence", 1.0), caliber=h.get("caliber"),
            )
            for h in ar.holes
        ],
        score_average=ar.score_average,
        created_at=ar.created_at.isoformat() if ar.created_at else None,
    )


def aggregate_for_user(
    *,
    user_uuid: UUID,
    recent_limit: int = 10,
    days: int = 30,
) -> AggregationDTO:
    """Compute the dashboard's hero stats + recent results + daily averages from
    the user's ``AcceptedResult`` rows (S-03 Phase 5).

    Pure-query logic; no HTTP. Three computations (per the plan's pinned
    semantics):

      - ``total_shots`` = the SUM of hole counts across the user's accepted
        results (NOT the count of result rows). A shooter who accepts one
        result with 10 holes has 10 shots, not 1.
      - ``last_session_average`` = the mean ``score_average`` across the most
        recent calendar day (server-local) with >=1 accepted result. Derived
        session = calendar day (the user's decision — morning + evening accepts
        on the same day are one session). ``None`` if no accepted results.
      - ``best_result`` = ``max(score_average)`` across the user's results.
        ``None`` if none.
      - ``recent`` = the ``recent_limit`` newest results as ``ResultSummaryDTO``.
      - ``daily_averages`` = for each of the last ``days`` days that has >=1
        accepted result, the mean ``score_average`` of that day's results
        (zero-result days omitted, matching the mocked fixture's chart shape).

    The dataset is small at MVP scale, so the hole-count sum is computed
    Python-side from the result rows already fetched for ``recent`` + the date
    group, rather than a JSON-length annotation (SQLite has no native
    JSON array-length).
    """
    user_results = AcceptedResult.objects.filter(user_uuid=user_uuid)

    # All of the user's results (small set at MVP scale), newest first, for
    # total_shots + best_result + the date grouping.
    all_rows = list(user_results.order_by("-created_at"))
    if not all_rows:
        return AggregationDTO(
            hero=HeroStatsDTO(
                total_shots=0,
                last_session_average=None,
                best_result=None,
            ),
            recent=[],
            daily_averages=[],
        )

    # total_shots = sum of hole counts across the user's results.
    total_shots = sum(len(r.holes) for r in all_rows)
    best_result = max(r.score_average for r in all_rows)

    # Derived session: the most recent calendar day with >=1 result.
    last_day = all_rows[0].created_at.date()
    last_day_rows = [r for r in all_rows if r.created_at.date() == last_day]
    last_session_average = (
        sum(r.score_average for r in last_day_rows) / len(last_day_rows)
    )

    # recent: newest recent_limit rows.
    recent = [
        ResultSummaryDTO(
            result_id=r.id,
            created_at=r.created_at.isoformat(),
            score_average=r.score_average,
            hole_count=len(r.holes),
            target_type=r.target_type,
        )
        for r in all_rows[:recent_limit]
    ]

    # daily_averages: mean score_average per calendar day, for the last `days`
    # days, omitting zero-result days. Built oldest-first so the chart reads
    # left-to-right chronologically.
    by_date: dict = {}
    for r in all_rows:
        d = r.created_at.date()
        by_date.setdefault(d, []).append(r.score_average)
    cutoff = (all_rows[0].created_at - timezone.timedelta(days=days)).date()
    daily = []
    for d in sorted(by_date.keys()):
        if d < cutoff:
            continue
        scores = by_date[d]
        daily.append(DailyAverageDTO(date=d.isoformat(), average=sum(scores) / len(scores)))

    return AggregationDTO(
        hero=HeroStatsDTO(
            total_shots=total_shots,
            last_session_average=last_session_average,
            best_result=best_result,
        ),
        recent=recent,
        daily_averages=daily,
    )


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
    """Map ``ScoringJob`` ORM row → ``ScoringJobDTO``.

    Raises ``ValueError`` if the stored ``result`` JSON is malformed (missing
    keys / wrong types) rather than silently substituting 0-defaults. The
    ``DetectionResult`` → dict → DTO hand-rebuild is fragile by construction
    (two parallel mappings); the validation makes drift loud. A future
    refactor should persist ``_to_result_dto(result).model_dump_json()`` at
    success time and ``model_validate_json()`` on read (one mapping, typed).
    """
    result_dto: Optional[ScoringResultDTO] = None
    if job.result:
        result_dict = job.result
        if isinstance(result_dict, dict) and result_dict.get("ok"):
            holes_list = result_dict.get("holes", [])
            holes: list[DetectedHoleDTO] = []
            for i, h in enumerate(holes_list):
                if not isinstance(h, dict) or not {"x", "y", "score"}.issubset(h):
                    raise ValueError(
                        f"ScoringJob {job.id} result.holes[{i}] malformed: {h!r}"
                    )
                holes.append(DetectedHoleDTO(
                    x=int(h["x"]),
                    y=int(h["y"]),
                    score=int(h["score"]),
                    confidence=float(h.get("confidence", 1.0)),
                    caliber=h.get("caliber"),
                ))
            result_dto = ScoringResultDTO(
                holes=holes,
                target_type=result_dict.get("target_type", "air_pistol"),
                notes=result_dict.get("notes"),
                detector_name=result_dict.get("detector", ""),
            )

    marked_image_url = None
    if job.marked_image_path:
        # Resolve the deliverable URL via the SAME storage that wrote it. The
        # marked-image path on the job is relative to ``ScoringStorage``'s root
        # (``MEDIA_ROOT/scoring`` under ``USE_S3=False``, or the S3 backend
        # under ``USE_S3=True``). Using the global ``default_storage`` here
        # would resolve against the wrong root under FS dev (default_storage
        # is rooted at ``MEDIA_ROOT``, not ``MEDIA_ROOT/scoring``). Under S3
        # ``ScoringStorage`` IS ``default_storage`` so the two coincide.
        storage = ScoringStorage()
        marked_image_url = storage._storage.url(job.marked_image_path)

    return ScoringJobDTO(
        job_id=job.id,
        status=job.status,
        target_type=job.target_type,
        caliber_hint=job.caliber_hint,
        distance=job.distance,
        weapon_type=job.weapon_type,
        result=result_dto,
        error=job.error,
        created_at=job.created_at.isoformat() if job.created_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        marked_image_url=marked_image_url,
    )
