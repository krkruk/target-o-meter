"""``/v1/scoring/jobs`` — the first BFF orchestration over the vision seam.

Two endpoints:

  - ``POST /v1/scoring/jobs`` — multipart upload (image + ``target_type`` +
    ``caliber_hint`` + ``distance_m``). Saves the upload via ``ScoringStorage``,
    then enqueues processing via ``schedule_image_processing`` inside a BFF-
    level ``transaction.atomic()`` (AGENTS.md §6.2 — the service's own atomic
    block is a nested savepoint; a failure after enqueue rolls the q2 task row
    back too because the broker is SQLite-on-default). Returns
    ``{job_id, status: "queued"}``.
  - ``GET /v1/scoring/jobs/{job_id}`` — reaps stuck jobs (PRD §Guardrail: no
    dead-end states), then reads the job via ``get_job`` (owner-only). The
    ``PermissionError`` from ``get_job`` (owner mismatch OR missing row — both
    look identical) maps to 404, not 403, so an ID-prober can't distinguish
    "exists, not mine" from "doesn't exist".

Both roles can upload (PRD FR-006/FR-007): the POST uses ``session_auth`` only
— ``require_owner`` is NOT applied. Per-job ownership is enforced on read by
``get_job``.

``distance`` + ``weapon_type`` are the S-03 FR-009 confirmation params,
collected in the wizard pre-upload and forwarded to
``schedule_image_processing`` (which persists them on the ``ScoringJob`` row).
They are metadata for the accept snapshot, not detector inputs.

Note: this module deliberately does NOT use ``from __future__ import
annotations``. With PEP 563 string annotations, ninja resolves forward refs
via ``func.__globals__`` — but ``@transaction.atomic`` wraps the view, so
``__globals__`` points at ``django.db.transaction``'s namespace, not ours, and
``Form`` / ``File`` / ``ScoringJobIn`` become unresolvable (silently
downgraded to query params). Real annotations sidestep the lookup entirely.
"""
import logging
from typing import Literal
from uuid import UUID

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpResponse
from ninja import Field, File, Form, Router, Schema, Status
from ninja.files import UploadedFile
from ninja.errors import HttpError

from src.bff.api import session_auth
from src.domains.identity.services import get_user_context
from src.domains.vision.dtos import (
    AcceptedResultDTO,
    AggregationDTO,
    DetectedHoleDTO,
    ErrorOut,
    ScoreListOut,
    ScoringJobDTO,
)
from src.domains.vision.pipeline.storage import ScoringStorage
from src.domains.vision.services import (
    StateError,
    accept_job,
    aggregate_for_user,
    delete_result,
    get_job,
    get_job_for_user,
    get_result,
    list_results,
    schedule_image_processing,
    update_result,
)


router = Router()

logger = logging.getLogger(__name__)


class ScoringJobIn(Schema):
    """Request DTO for ``POST /v1/scoring/jobs``.

    ``target_type`` is a ``Literal`` so django-ninja/Pydantic rejects anything
    else with **422 at the BFF boundary** — do NOT rely on the ORM's
    ``CharField`` (it has no ``choices=``, so without this Pydantic guard an
    invalid value would save cleanly and only blow up inside ``process_image``
    when the worker runs).
    """

    target_type: Literal["air_pistol", "precision_pistol"] = "air_pistol"
    caliber_hint: str | None = None  # free-text; the UI taxonomy lives client-side
    distance: int | None = None      # S-03 FR-009 confirmation param (meters)
    weapon_type: str | None = None   # S-03 FR-009 confirmation param (free-text; ISSF values client-side)


class ScoringJobOut(Schema):
    """Response DTO for ``POST /v1/scoring/jobs``."""

    job_id: str
    status: str


@router.post("/scoring/jobs", auth=session_auth, response={201: ScoringJobOut})
@transaction.atomic
def create_scoring_job(
    request,
    details: Form[ScoringJobIn],
    file: File[UploadedFile],
) -> ScoringJobOut:
    """Multipart upload → enqueue. Both Owner and User roles can upload.

    TODO(S-03): no per-user submission rate limit. A logged-in user can POST
    at arbitrary rate, each enqueuing a q2 task; the queue is capped at 50 /
    3 workers, so one user can saturate scoring for everyone. Acceptable for
    single-user MVP; S-03 should add a per-user QUEUED+RUNNING ceiling
    (count rows owned by user_uuid, reject 429 above ~5). See S-02
    impl-review F7.
    """
    try:
        user_dto = get_user_context(str(request.user.sub))
    except get_user_model().DoesNotExist:
        raise HttpError(401, "Session user no longer exists") from None

    # Phase 8.12: stage-by-stage logging so the upload pipeline is legible in
    # ``railway logs``. The 500 currently fires somewhere in save_upload →
    # schedule_image_processing and, with no LOGGING dict, raised silently.
    # Each stage logs on success; a 500 with no later stage line localizes the
    # failure to the stage that DIDN'T log (the next one). File bytes are read
    # ONCE (file.read() can't be re-read) — log the size, not a second read.
    upload_bytes = file.read()
    logger.info(
        "scoring job: upload received user_uuid=%s filename=%r size=%d "
        "target_type=%s",
        user_dto.user_uuid, file.name, len(upload_bytes), details.target_type,
    )

    storage = ScoringStorage()
    input_path = storage.save_upload(upload_bytes, file.name)
    logger.info(
        "scoring job: upload saved user_uuid=%s stored_path=%s",
        user_dto.user_uuid, input_path,
    )

    job_id = schedule_image_processing(
        user_uuid=user_dto.user_uuid,
        input_path=input_path,
        target_type=details.target_type,  # narrowed by vision's TargetType
        caliber_hint=details.caliber_hint,
        distance=details.distance,
        weapon_type=details.weapon_type,
    )
    logger.info(
        "scoring job: enqueued job_id=%s user_uuid=%s",
        job_id, user_dto.user_uuid,
    )
    return ScoringJobOut(job_id=job_id, status="queued")


@router.get(
    "/scoring/jobs/{job_id}", auth=session_auth, response={200: ScoringJobDTO}
)
def get_scoring_job(request, job_id: str) -> ScoringJobDTO:
    """Read a job. Owner-only — 404 on mismatch OR missing.

    Reaping of stuck RUNNING rows is NOT done here: it runs on a django-q2
    Schedule (``reap-stuck-scoring-jobs`` row, registered by the vision
    migration 0003) every 60s. Keeping the read path off the SQLite write lock
    matters under the 1500ms client poll — see S-02 impl-review F3. The
    ``STUCK_RUNNING_TIMEOUT_SECONDS`` (1200s) >> the 60s cadence, so a stuck
    job still resolves within ≤60s of staleness, far under the reap window.
    """
    try:
        user_dto = get_user_context(str(request.user.sub))
    except get_user_model().DoesNotExist:
        raise HttpError(401, "Session user no longer exists") from None

    try:
        return get_job(job_id, user_dto.user_uuid)
    except PermissionError:
        # ID-probers can't distinguish "exists, not mine" from "doesn't exist".
        raise HttpError(404, "Not found") from None


@router.get("/scoring/jobs/{job_id}/marked-image", auth=session_auth)
def get_scoring_job_marked_image(request, job_id: str):
    """Stream a job's marked-image artifact back as ``image/png`` (S-03 Phase 7).

    Resource-named per the API-design lesson: the job's marked-image ARTIFACT
    (a noun), NOT a verb. The browser fetches this same-origin route instead of
    a presigned S3 URL — so ``AWSAccessKeyId``/``Signature`` and the internal
    ``minio:9000`` endpoint never reach the client. The bytes are read
    server-side via ``ScoringStorage.read_deliverable_bytes`` and streamed
    through an ``HttpResponse`` (the house style from ``dev_vite_proxy``).

    Ownership mirrors ``GET /v1/scoring/jobs/{id}``: 404 on mismatch OR missing
    (ID-prober learns nothing). 404 when the job has no marked image yet
    (queued/running), not 500. No Pydantic ``response=``: this route returns
    raw bytes, not a JSON DTO.
    """
    try:
        user_dto = get_user_context(str(request.user.sub))
    except get_user_model().DoesNotExist:
        raise HttpError(401, "Session user no longer exists") from None

    try:
        job = get_job_for_user(job_id, user_dto.user_uuid)
    except PermissionError:
        raise HttpError(404, "Not found") from None

    if not job.marked_image_path:
        # Queued/running — no marked image written yet.
        raise HttpError(404, "Not found") from None

    storage = ScoringStorage()
    data = storage.read_deliverable_bytes(job.marked_image_path)
    return HttpResponse(data, content_type="image/png")


class AcceptResultIn(Schema):
    """Request body for ``POST /v1/scoring/results`` (S-03 Phase 2).

    JSON, not multipart — this route accepts a detection result, it doesn't
    upload an image. ``job_id`` rides in the body because the resource-named
    route (``/scoring/results``, the accepted-result resource per the API-
    design lesson) has no ``{job_id}`` path param. ``holes`` requires ≥1 entry
    (a score snapshot of zero holes is meaningless for aggregation).
    """

    job_id: UUID
    target_type: Literal["air_pistol", "precision_pistol"] = "air_pistol"
    caliber_hint: str | None = None
    distance: int | None = None
    weapon_type: str | None = None
    holes: list[DetectedHoleDTO] = Field(min_length=1)


@router.post(
    "/scoring/results",
    auth=session_auth,
    response={201: AcceptedResultDTO, 200: AcceptedResultDTO},
)
@transaction.atomic
def accept_scoring_result(request, payload: AcceptResultIn):
    """Accept a succeeded job's detection result → create an immutable
    ``AcceptedResult`` snapshotting the confirmed params + corrected holes +
    computed score (FR-010). Idempotent: re-POST for the same job returns the
    existing row (200) instead of a duplicate (201).

    Error mapping mirrors the existing routes (service owns the rule, route
    translates the exception to HTTP):
      - ``PermissionError`` (missing / ownership mismatch) → 404 (identical to
        ``GET /scoring/jobs/{id}`` so an ID-prober learns nothing).
      - ``StateError`` (job not SUCCEEDED) → 409 Conflict.
    """
    try:
        user_dto = get_user_context(str(request.user.sub))
    except get_user_model().DoesNotExist:
        raise HttpError(401, "Session user no longer exists") from None

    try:
        dto, created = accept_job(
            job_id=payload.job_id,
            user_uuid=user_dto.user_uuid,
            target_type=payload.target_type,
            caliber_hint=payload.caliber_hint,
            distance=payload.distance,
            weapon_type=payload.weapon_type,
            holes=payload.holes,
        )
    except PermissionError:
        raise HttpError(404, "Not found") from None
    except StateError:
        raise HttpError(409, "Job not succeeded") from None

    # 201 on first accept (a new row was inserted); 200 on idempotent re-POST
    # or race-loser (an existing row was returned). ``created`` is signalled by
    # the service from the create-vs-refetch branch — no fragile timestamp
    # comparison. ``Status(...)`` is django-ninja's non-deprecated multi-code
    # response form (a bare tuple is deprecated).
    return Status(201 if created else 200, dto)


@router.get(
    "/scores/aggregations", auth=session_auth, response={200: AggregationDTO}
)
def get_aggregations(request) -> AggregationDTO:
    """The dashboard's single aggregation endpoint (S-03 Phase 5, FR-012).

    Resource-named per the API-design lesson (``/scores/aggregations``, plural
    noun, NOT ``/scoring/aggregate``). Lives under ``/v1/scores/`` to distinguish
    the aggregated-result resource from the ``/v1/scoring/jobs`` pipeline
    resource. Returns hero stats + a recent-results list + a daily-average
    chart, computed on read from the user's ``AcceptedResult`` rows.

    An owner sees their own aggregations (the owner is also a shooter per PRD
    §Access Control) — no special owner path.
    """
    try:
        user_dto = get_user_context(str(request.user.sub))
    except get_user_model().DoesNotExist:
        raise HttpError(401, "Session user no longer exists") from None

    return aggregate_for_user(user_uuid=user_dto.user_uuid)


@router.get("/scores", auth=session_auth, response={200: ScoreListOut})
def list_scores(request, page: int = 1, page_size: int = 20) -> ScoreListOut:
    """The user's score list (the ``/v1/scores`` resource, ``user-score-dashboard``
    change). Paginated, newest first, per-user isolated (the caller sees only
    their own rows). Resource-named per the API-design lesson.

    Registered BEFORE ``GET /scores/{result_id}`` (param) so the bare list route
    resolves cleanly — the param route below can't shadow ``/scores/aggregations``
    (the literal route above this) because it sits last. django-ninja matches in
    declaration order; the existing ``GET /scores/aggregations`` must stay
    registered before any ``GET /scores/{result_id}`` or ``aggregations`` parses
    as a UUID and 422s.
    """
    try:
        user_dto = get_user_context(str(request.user.sub))
    except get_user_model().DoesNotExist:
        raise HttpError(401, "Session user no longer exists") from None

    return list_results(user_uuid=user_dto.user_uuid, page=page, page_size=page_size)


@router.get(
    "/scores/{result_id}",
    auth=session_auth,
    response={200: AcceptedResultDTO, 404: ErrorOut},
)
def get_score(request, result_id: UUID) -> AcceptedResultDTO:
    """Read one accepted result (the Modify modal fetches this — the accepted/
    corrected snapshot, NOT the raw detector output which would clobber a prior
    correction). Owner-only — 404 on mismatch OR missing (ID-prober invariant).

    Registered LAST under ``/scores/`` so the literal ``/scores/aggregations``
    route (the home Dashboard's aggregation call) is matched first.
    """
    try:
        user_dto = get_user_context(str(request.user.sub))
    except get_user_model().DoesNotExist:
        raise HttpError(401, "Session user no longer exists") from None

    try:
        return get_result(result_id=result_id, user_uuid=user_dto.user_uuid)
    except PermissionError:
        raise HttpError(404, "Not found") from None


class ScoreUpdateIn(Schema):
    """Request body for ``PATCH /v1/scores/{result_id}`` (the dashboard's Modify
    flow). Mirrors ``AcceptResultIn`` minus ``job_id`` — the resource is named
    by the path param. ``holes`` requires >=1 entry (guards the mean recompute
    against a divide-by-zero; mirrors ``AcceptResultIn.holes``). Optional params
    override when provided (``None`` leaves the stored value).
    """

    holes: list[DetectedHoleDTO] = Field(min_length=1)
    # Literal guard mirrors ScoringJobIn (L84) / AcceptResultIn (L224); the
    # model CharField has no choices=, so the BFF is the only gate.
    target_type: Literal["air_pistol", "precision_pistol"] | None = None
    caliber_hint: str | None = None
    distance: int | None = None
    weapon_type: str | None = None


@router.patch(
    "/scores/{result_id}",
    auth=session_auth,
    response={200: AcceptedResultDTO, 404: ErrorOut},
)
@transaction.atomic
def patch_score(request, result_id: UUID, payload: ScoreUpdateIn) -> AcceptedResultDTO:
    """Modify an accepted result — persist edited holes + recompute
    ``score_average`` (PRD FR-010 Socrates amendment for the
    ``user-score-dashboard`` change). Owner-only — 404 on mismatch OR missing
    (ID-prober invariant). ``updated_at`` advances on the service's save.
    """
    try:
        user_dto = get_user_context(str(request.user.sub))
    except get_user_model().DoesNotExist:
        raise HttpError(401, "Session user no longer exists") from None

    try:
        return update_result(
            result_id=result_id, user_uuid=user_dto.user_uuid,
            holes=payload.holes,
            target_type=payload.target_type,
            caliber_hint=payload.caliber_hint,
            distance=payload.distance,
            weapon_type=payload.weapon_type,
        )
    except PermissionError:
        raise HttpError(404, "Not found") from None


@router.delete(
    "/scores/{result_id}",
    auth=session_auth,
    response={204: None, 404: ErrorOut},
)
def delete_score(request, result_id: UUID):
    """Hard-delete an accepted result + best-effort remove its storage objects;
    retain the ``ScoringJob`` audit row. Owner-only — 404 on mismatch OR missing
    (ID-prober invariant). Mirrors ``delete_a_user`` (204 on success).
    """
    try:
        user_dto = get_user_context(str(request.user.sub))
    except get_user_model().DoesNotExist:
        raise HttpError(401, "Session user no longer exists") from None

    try:
        delete_result(result_id=result_id, user_uuid=user_dto.user_uuid)
    except PermissionError:
        raise HttpError(404, "Not found") from None
    return Status(204, None)
