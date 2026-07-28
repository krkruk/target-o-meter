"""MockDetector tests — S-03 random-N-hole pattern (replaces the fixed 5-hole).

The mock now emits a configurable count of random holes (default 10) with
scores 0–10, random (x,y) in the 1024×1024 frame, and random confidence. The
seed (env ``MOCK_DETECTOR_SEED``) makes the pattern deterministic in tests;
without it the dev path varies per run so the accept→persist→aggregate
round-trip exercises varied data.

Tests pin the new shape + the seed determinism contract. The seed + count are
read at detect-time from the environment (not ``__init__``) so
``DetectorFactory.build("mock")`` stays parameterless.
"""
from __future__ import annotations

import numpy as np

from src.domains.vision.detectors.mock_detector import MockDetector


_IMAGE = np.zeros((1024, 1024), dtype=np.uint8)


def test_mock_detector_name_is_mock() -> None:
    assert MockDetector().name == "mock"


def test_mock_detector_ignores_target_ring1_px(monkeypatch) -> None:
    # The mock must accept and ignore target_ring1_px (Phase-3 Step-2 contract).
    monkeypatch.setenv("MOCK_DETECTOR_SEED", "42")
    result = MockDetector().detect(
        _IMAGE, target_type="air_pistol", target_ring1_px=394.0,
    )
    assert len(result.holes) >= 1


def test_mock_detector_default_hole_count_is_10(monkeypatch) -> None:
    """Without ``MOCK_DETECTOR_HOLE_COUNT`` the default is 10 holes."""
    monkeypatch.setenv("MOCK_DETECTOR_SEED", "42")
    monkeypatch.delenv("MOCK_DETECTOR_HOLE_COUNT", raising=False)
    result = MockDetector().detect(_IMAGE, target_type="air_pistol")
    assert len(result.holes) == 10


def test_mock_detector_hole_count_from_env(monkeypatch) -> None:
    """``MOCK_DETECTOR_HOLE_COUNT`` sets the count."""
    monkeypatch.setenv("MOCK_DETECTOR_SEED", "42")
    monkeypatch.setenv("MOCK_DETECTOR_HOLE_COUNT", "7")
    result = MockDetector().detect(_IMAGE, target_type="air_pistol")
    assert len(result.holes) == 7


def test_mock_detector_each_hole_in_valid_ranges(monkeypatch) -> None:
    """Every hole: 0 ≤ score ≤ 10, 0 ≤ x/y ≤ 1024, 0.5 ≤ confidence ≤ 0.99."""
    monkeypatch.setenv("MOCK_DETECTOR_SEED", "42")
    monkeypatch.setenv("MOCK_DETECTOR_HOLE_COUNT", "20")
    result = MockDetector().detect(_IMAGE, target_type="air_pistol")
    assert len(result.holes) == 20
    for h in result.holes:
        assert 0 <= h.score <= 10
        assert 0 <= h.x <= 1024
        assert 0 <= h.y <= 1024
        assert 0.5 <= h.confidence <= 0.99


def test_mock_detector_seed_is_deterministic(monkeypatch) -> None:
    """Same seed → same hole pattern (x, y, score, confidence per hole)."""
    monkeypatch.setenv("MOCK_DETECTOR_SEED", "123")
    monkeypatch.setenv("MOCK_DETECTOR_HOLE_COUNT", "6")
    r1 = MockDetector().detect(_IMAGE, target_type="air_pistol")
    r2 = MockDetector().detect(_IMAGE, target_type="air_pistol")
    assert len(r1.holes) == len(r2.holes) == 6
    for a, b in zip(r1.holes, r2.holes):
        assert (a.x, a.y, a.score, a.confidence) == (b.x, b.y, b.score, b.confidence)


def test_mock_detector_different_seeds_differ(monkeypatch) -> None:
    """Two different seeds produce different patterns (the seed actually drives
    the RNG, not a no-op)."""
    monkeypatch.setenv("MOCK_DETECTOR_HOLE_COUNT", "10")
    monkeypatch.setenv("MOCK_DETECTOR_SEED", "1")
    r1 = MockDetector().detect(_IMAGE, target_type="air_pistol")
    monkeypatch.setenv("MOCK_DETECTOR_SEED", "2")
    r2 = MockDetector().detect(_IMAGE, target_type="air_pistol")
    p1 = [(h.x, h.y, h.score) for h in r1.holes]
    p2 = [(h.x, h.y, h.score) for h in r2.holes]
    assert p1 != p2
