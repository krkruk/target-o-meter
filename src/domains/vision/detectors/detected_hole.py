"""A single detected hole in 1024x1024 normalized image coordinates.

Ported verbatim from ``cv/detector_base.py:23-48`` (commit 76f6fc4) — the
Phase-3 Step-2 contract the locked model already speaks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class DetectedHole:
    """Coordinates are raw pixels in the normalized frame (bullseye at 512, 512;
    1-ring boundary at radius 500). Score is the ISSF score 0..10 (LLM-provided
    per the user's Q5/Q7 direction; classical scoring is computed separately in
    the pipeline for diagnostic comparison). ``caliber`` is the LLM's per-hole
    caliber guess (free text); it sizes the magenta marker, never affects score.
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
