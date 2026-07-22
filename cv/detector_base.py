"""Strategy interface for hole detection on a 1024x1024 normalized ISSF target image.

Implementations:
- cv.mock_detector.MockDetector       — fixed pattern, for plumbing tests
- cv.langchain_detector.* (Phase 3)   — Gemma 4 via LangChain (AI Studio or Ollama)

The detector ONLY sees the normalized image + metadata hints, and returns hole
positions + scores in the 1024x1024 frame. All geometry (localization,
calibration, warp, coordinate inversion) is handled outside the strategy —
so swapping detectors never breaks the transform chain.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np

TargetType = Literal["air_pistol", "precision_pistol"]


@dataclass
class DetectedHole:
    """A single detected hole, in 1024x1024 normalized image coordinates.

    Coordinates are raw pixels in the normalized frame (bullseye at 512, 512;
    1-ring boundary at radius 500). Score is the ISSF score 0..10 (LLM-provided
    per the user's Q5/Q7 direction; classical scoring is computed separately in
    the pipeline for diagnostic comparison). ``caliber`` is the LLM's per-hole
    caliber guess (free text); it sizes the magenta marker, never affects score.
    Added in Phase 3 Step 2 so the user's per-hole JSON output carries the
    caliber the LLM reports (per the Step-2 3-file spec).
    """
    x: int
    y: int
    score: int
    confidence: float = 1.0
    caliber: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "x": int(self.x),
            "y": int(self.y),
            "score": int(self.score),
            "confidence": float(self.confidence),
            "caliber": self.caliber,
        }


@dataclass
class DetectionResult:
    """Output of any HoleDetector strategy. Shape is identical across detectors."""
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


class HoleDetector(ABC):
    """Strategy interface — see module docstring."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in result JSONs and logs."""

    @abstractmethod
    def detect(
        self,
        image_1024: np.ndarray,
        target_type: TargetType,
        caliber_hint: Optional[str] = None,
        target_ring1_px: Optional[float] = None,
    ) -> DetectionResult:
        """Run detection on a 1024x1024 normalized target image.

        Args:
            image_1024: uint8 grayscale, shape (1024, 1024). Bullseye at (512, 512).
            target_type: "air_pistol" | "precision_pistol".
            caliber_hint: optional string like "9x19" / "22lr" / ".223Rem" / "slug".
            target_ring1_px: radius of the outermost (ring-1) printed ring from
                the bullseye, in 1024-frame px. Needed by the LLM detector to
                build its prompt (numeric ring step) and to size caliber→px
                magenta markers. None for detectors that ignore geometry
                (MockDetector). Added in Phase 3 Step 2 (handoff subtlety #1).

        Returns:
            DetectionResult with holes in 1024x1024 frame + LLM-provided scores.
        """
