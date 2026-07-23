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
    result: Optional[ScoringResultDTO] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
