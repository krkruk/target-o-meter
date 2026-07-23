"""Mock hole detector — fixed 5-hole pattern for deterministic plumbing tests.

Ported verbatim from ``cv/mock_detector.py`` (commit 76f6fc4). The pattern is a
5-hole "bullseye + cardinals" arrangement in 1024x1024 coords:

    - 1 hole at the bullseye (512, 512)         score 10
    - 4 holes at d=200 in cardinal directions   score 7
        (712, 512) (312, 512) (512, 312) (512, 712)

The mock always returns this same pattern regardless of input image, so the
pipeline's job is to:

  1. Produce a 1024x1024 image where this pattern would visually make sense
     (bullseye at center, ring 1 at radius 500).
  2. Invert these 5 points back to source-image coordinates.
  3. Draw magenta dots at the inverted positions on the source image.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from src.domains.vision.detectors.detected_hole import DetectedHole
from src.domains.vision.detectors.detection_result import DetectionResult
from src.domains.vision.ports import HoleDetector, TargetType


_MOCK_HOLES = [
    DetectedHole(x=512, y=512, score=10, confidence=1.00),
    DetectedHole(x=712, y=512, score=7, confidence=0.90),
    DetectedHole(x=312, y=512, score=7, confidence=0.90),
    DetectedHole(x=512, y=312, score=7, confidence=0.90),
    DetectedHole(x=512, y=712, score=7, confidence=0.90),
]


class MockDetector(HoleDetector):
    """Returns the same fixed 5-hole pattern for any input."""

    @property
    def name(self) -> str:
        return "mock"

    def detect(
        self,
        image_1024: np.ndarray,
        target_type: TargetType,
        caliber_hint: Optional[str] = None,
        target_ring1_px: Optional[float] = None,
    ) -> DetectionResult:
        # target_ring1_px is accepted and ignored — the mock returns a fixed
        # pattern and needs no ring geometry. (Phase 3 Step 2 signature extension.)
        del image_1024, caliber_hint, target_ring1_px  # accepted but unused
        return DetectionResult(
            holes=[
                DetectedHole(
                    x=h.x,
                    y=h.y,
                    score=h.score,
                    confidence=h.confidence,
                )
                for h in _MOCK_HOLES
            ],
            target_type=target_type,
            detector_name=self.name,
            notes="Mock detector: fixed 5-hole pattern (1 bullseye + 4 cardinals at d=200, ring ~7).",
            raw={"pattern": "bullseye+cardinals", "n": len(_MOCK_HOLES)},
        )
