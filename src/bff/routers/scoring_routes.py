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

``distance_m`` is intentionally NOT forwarded to ``schedule_image_processing``
(which has no such param) — it is a BFF-level mock field, dropped on the floor
in S-02, promoted to a real ``ScoringJob.distance`` column in S-03 (FR-009).

Note: this module deliberately does NOT use ``from __future__ import
annotations``. With PEP 563 string annotations, ninja resolves forward refs
via ``func.__globals__`` — but ``@transaction.atomic`` wraps the view, so
``__globals__`` points at ``django.db.transaction``'s namespace, not ours, and
``Form`` / ``File`` / ``ScoringJobIn`` become unresolvable (silently
downgraded to query params). Real annotations sidestep the lookup entirely.
"""
from typing import Literal

from django.contrib.auth import get_user_model
from django.db import transaction
from ninja import File, Form, Router, Schema
from ninja.errors import HttpError
from ninja.files import UploadedFile

from src.bff.api import session_auth
from src.domains.identity.services import get_user_context
from src.domains.vision.dtos import ScoringJobDTO
from src.domains.vision.pipeline.storage import ScoringStorage
from src.domains.vision.services import (
    get_job,
    reap_stuck_jobs,
    schedule_image_processing,
)


router = Router()


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
    distance_m: int | None = None     # BFF-level mock field; vision has no distance concept


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
    """Multipart upload → enqueue. Both Owner and User roles can upload."""
    try:
        user_dto = get_user_context(str(request.user.sub))
    except get_user_model().DoesNotExist:
        raise HttpError(401, "Session user no longer exists") from None

    storage = ScoringStorage()
    input_path = storage.save_upload(file.read(), file.name)
    job_id = schedule_image_processing(
        user_uuid=user_dto.user_uuid,
        input_path=input_path,
        target_type=details.target_type,  # narrowed by vision's TargetType
        caliber_hint=details.caliber_hint,
    )
    return ScoringJobOut(job_id=job_id, status="queued")


@router.get(
    "/scoring/jobs/{job_id}", auth=session_auth, response={200: ScoringJobDTO}
)
def get_scoring_job(request, job_id: str) -> ScoringJobDTO:
    """Reap stuck jobs then read. Owner-only — 404 on mismatch OR missing."""
    try:
        user_dto = get_user_context(str(request.user.sub))
    except get_user_model().DoesNotExist:
        raise HttpError(401, "Session user no longer exists") from None

    reap_stuck_jobs()  # PRD §Guardrail: no dead-end states
    try:
        return get_job(job_id, user_dto.user_uuid)
    except PermissionError:
        # ID-probers can't distinguish "exists, not mine" from "doesn't exist".
        raise HttpError(404, "Not found") from None
