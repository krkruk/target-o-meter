"""MockDetector tests — verify the fixed 5-hole bullseye+cardinals pattern.

This is the first RED/GREEN/REFACTOR loop of the cv-service-boundary change
(per ``/10x-tdd``): the test pins the contract before the production code is
ported.
"""
from __future__ import annotations

import numpy as np

from src.domains.vision.detectors.mock_detector import MockDetector


def test_mock_detector_returns_5_holes_with_documented_scores() -> None:
    detector = MockDetector()
    image = np.zeros((1024, 1024), dtype=np.uint8)
    result = detector.detect(image, target_type="air_pistol")

    scores = sorted(h.score for h in result.holes)
    assert scores == [7, 7, 7, 7, 10]
    assert len(result.holes) == 5


def test_mock_detector_name_is_mock() -> None:
    assert MockDetector().name == "mock"


def test_mock_detector_bullseye_at_512_512() -> None:
    result = MockDetector().detect(
        np.zeros((1024, 1024), dtype=np.uint8),
        target_type="air_pistol",
    )
    bullseye = next(h for h in result.holes if h.score == 10)
    assert (bullseye.x, bullseye.y) == (512, 512)


def test_mock_detector_ignores_target_ring1_px() -> None:
    # The mock must accept and ignore target_ring1_px (Phase-3 Step-2 contract).
    result = MockDetector().detect(
        np.zeros((1024, 1024), dtype=np.uint8),
        target_type="air_pistol",
        target_ring1_px=394.0,
    )
    assert len(result.holes) == 5
