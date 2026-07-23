"""Output of any ``HoleDetector`` strategy.

Ported verbatim from ``cv/detector_base.py:51-68`` (commit 76f6fc4). Shape is
identical across detectors (the seam that lets the pipeline swap strategies).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.domains.vision.detectors.detected_hole import DetectedHole
from src.domains.vision.ports import TargetType


@dataclass
class DetectionResult:
    """Strategy-agnostic result envelope."""

    holes: list[DetectedHole]
    target_type: TargetType
    detector_name: str
    notes: Optional[str] = None
    raw: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "holes": [h.to_dict() for h in self.holes],
            "target_type": self.target_type,
            "detector_name": self.detector_name,
            "notes": self.notes,
            "count": len(self.holes),
            "total": sum(h.score for h in self.holes),
        }
