"""Mock hole detector — random N-hole pattern (S-03).

Replaces the S-02 fixed 5-hole bullseye+cardinals pattern. The mock now emits
a configurable count of random holes so the accept→persist→aggregate round-trip
exercises varied data (multi-hole sums, max scores) instead of a constant
pattern.

Configuration (read at detect-time from the environment, NOT ``__init__``, so
``DetectorFactory.build("mock")`` stays parameterless — the factory is called
in ``process_image`` with no args):

  - ``MOCK_DETECTOR_HOLE_COUNT`` (default 10) — how many holes to emit.
  - ``MOCK_DETECTOR_SEED`` — if set, the pattern is deterministic (seeded
    ``random.Random(seed)``); if unset, the module-level RNG varies per run.
    Tests set a seed for stable assertions; the dev path leaves it unset so two
    consecutive jobs differ.

Each hole:
  - ``x``, ``y`` random in ``[50, 974]`` (inset so holes land on the target
    face, not the edge of the 1024×1024 frame),
  - ``score`` random in ``[0, 10]`` (the PRD's 0–10 scoring domain),
  - ``confidence`` random in ``[0.5, 0.99]``.
"""
from __future__ import annotations

import os
import random
from typing import Optional

import numpy as np

from src.domains.vision.detectors.detected_hole import DetectedHole
from src.domains.vision.detectors.detection_result import DetectionResult
from src.domains.vision.ports import HoleDetector, TargetType


_DEFAULT_HOLE_COUNT = 10
# Inset so holes land on the target face, not the frame edge.
_XY_MIN, _XY_MAX = 50, 974
_CONF_MIN, _CONF_MAX = 0.5, 0.99
_SCORE_MIN, _SCORE_MAX = 0, 10


class MockDetector(HoleDetector):
    """Random N-hole pattern; seedable for deterministic tests."""

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
        # target_ring1_px is accepted and ignored — the mock needs no ring
        # geometry. (Phase 3 Step 2 signature extension.)
        del image_1024, caliber_hint, target_ring1_px  # accepted but unused

        # Read config at detect-time so the factory stays parameterless.
        count = int(os.environ.get("MOCK_DETECTOR_HOLE_COUNT", _DEFAULT_HOLE_COUNT))
        seed = os.environ.get("MOCK_DETECTOR_SEED")
        rng = random.Random(seed) if seed is not None else random

        holes = [
            DetectedHole(
                x=rng.randint(_XY_MIN, _XY_MAX),
                y=rng.randint(_XY_MIN, _XY_MAX),
                score=rng.randint(_SCORE_MIN, _SCORE_MAX),
                confidence=round(rng.uniform(_CONF_MIN, _CONF_MAX), 4),
            )
            for _ in range(count)
        ]

        return DetectionResult(
            holes=holes,
            target_type=target_type,
            detector_name=self.name,
            notes=(
                f"Mock detector: random N-hole pattern "
                f"(seed={seed or 'unseeded'}, count={count})."
            ),
            raw={"pattern": "random-n", "n": count, "seed": seed},
        )
