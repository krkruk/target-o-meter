"""Pydantic DTO contracts for the vision domain.

All data crossing the domain boundary (inter-domain communication and API
responses) is expressed here as Pydantic models (AGENTS.md §5 — DTOs only).
Internal dataclasses (``DetectedHole``, ``DetectionResult``) live in
``detectors/``; services maps them to these DTOs at the seam.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from src.domains.vision.ports import TargetType


class DetectedHoleDTO(BaseModel):
    """A single detected hole, in 1024x1024 normalized image coordinates."""

    x: int
    y: int
    score: int
    confidence: float
    caliber: Optional[str] = None


class ScoringResultDTO(BaseModel):
    """Pipeline output crossing the vision → BFF seam."""

    holes: list[DetectedHoleDTO]
    target_type: TargetType
    notes: Optional[str] = None
    detector_name: str


class ScoringJobDTO(BaseModel):
    """Read accessor result for a ScoringJob.

    Finalized in Phase 5 once the ORM model exists; fields here are the
    contract the BFF reads.
    """

    job_id: UUID
    status: str
    target_type: TargetType
    caliber_hint: Optional[str] = None
    # S-03 FR-009 confirmation params (mirror of ScoringJob's columns). Surfaced
    # so the SPA's ``/results/:jobId`` screen can pre-fill the accept form with
    # the wizard's selections before the user accepts.
    distance: Optional[int] = None
    weapon_type: Optional[str] = None
    result: Optional[ScoringResultDTO] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    # S-02 Phase 4.0: the marked-image deliverable URL, populated from
    # ``job.marked_image_path`` via ``default_storage.url(...)`` only after
    # ``process_image`` has written the deliverable and flipped status=SUCCEEDED.
    # ``None`` for queued/running jobs (no deliverable yet) so the GET doesn't
    # 500 on the missing path. Under ``USE_S3=False`` dev this is a
    # ``MEDIA_URL``-rooted URL the SPA fetches directly; under the docker-compose
    # MinIO path it's a MinIO URL. The Tigris/prod presigned-URL policy is an
    # S-03 concern (lands alongside the OpenCV+S3 refactor).
    marked_image_url: Optional[str] = None


class AcceptedResultDTO(BaseModel):
    """The wire contract for an accepted detection result (S-03 Phase 2).

    Returned by ``POST /v1/scoring/results`` on both first-accept (201) and
    idempotent re-POST (200). Snapshots the confirmed params + corrected-hole
    list + the mean score (the value aggregation reads).
    """

    result_id: UUID
    source_job: UUID
    target_type: TargetType
    caliber_hint: Optional[str] = None
    distance: Optional[int] = None
    weapon_type: Optional[str] = None
    holes: list[DetectedHoleDTO]
    score_average: float
    created_at: Optional[str] = None
