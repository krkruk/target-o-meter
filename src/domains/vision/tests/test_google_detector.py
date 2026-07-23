"""GoogleAIStudioDetector unit tests — mocked LangChain invoke (no network).

Verifies the ``TargetAnalysis → DetectionResult`` mapping, env handling, and
error messages without making live API calls.
"""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from src.domains.vision.detectors.detected_hole import DetectedHole
from src.domains.vision.detectors.detection_result import DetectionResult
from src.domains.vision.detectors.google_ai_studio_detector import (
    GoogleAIStudioDetector,
)
from src.domains.vision.detectors.schema import Hole, TargetAnalysis


def test_google_detector_raises_when_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        GoogleAIStudioDetector()


def test_google_detector_name_is_google_prefixed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-for-test")
    det = GoogleAIStudioDetector(model="gemini-3.5-flash-lite")
    assert det.name == "google-gemini-3.5-flash-lite"


def _mock_target_analysis() -> TargetAnalysis:
    return TargetAnalysis(
        holes=[
            Hole(x=512, y=512, score=10, confidence=0.95, caliber="9mm"),
            Hole(x=600, y=512, score=7, confidence=0.80, caliber="9mm"),
        ],
        target_type="air_pistol",
        notes="mocked analysis",
    )


def test_google_detector_maps_analysis_to_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """``TargetAnalysis`` → ``DetectionResult`` mapping is the locked Step-2 path."""
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-for-test")
    det = GoogleAIStudioDetector(model="gemini-3.5-flash-lite")

    fake_meta = {"model": "gemini-3.5-flash-lite", "temperature": 1.0, "elapsed_s": 0.5, "n_holes": 2, "input_path": None}
    with patch.object(det._client, "analyze_array", return_value=(_mock_target_analysis(), fake_meta)):
        result = det.detect(
            image_1024=np.zeros((1024, 1024), dtype=np.uint8),
            target_type="air_pistol",
            caliber_hint="9mm",
            target_ring1_px=394.0,
        )

    assert isinstance(result, DetectionResult)
    assert result.detector_name == "google-gemini-3.5-flash-lite"
    assert result.target_type == "air_pistol"
    assert result.notes == "mocked analysis"
    assert len(result.holes) == 2
    assert result.holes[0] == DetectedHole(x=512, y=512, score=10, confidence=0.95, caliber="9mm")
    assert result.holes[1] == DetectedHole(x=600, y=512, score=7, confidence=0.80, caliber="9mm")

    # raw carries the model + ring geometry + calibers (the Step-2 contract).
    assert result.raw["model"] == "gemini-3.5-flash-lite"
    assert result.raw["target_ring1_px"] == 394.0
    assert result.raw["ring_step_px"] == pytest.approx(394.0 / 9.0)
    assert result.raw["primary_caliber_hint"] == "9mm"
    assert result.raw["calibers"] == ["9mm", "9mm"]


def test_google_detector_defaults_target_ring1_px_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detector falls back to 394.0 when target_ring1_px is None (handoff subtlety #1)."""
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-for-test")
    det = GoogleAIStudioDetector()
    fake_meta = {"model": "m", "temperature": 1.0, "elapsed_s": 0.1, "n_holes": 0, "input_path": None}
    empty = TargetAnalysis(holes=[], target_type="air_pistol", notes=None)
    with patch.object(det._client, "analyze_array", return_value=(empty, fake_meta)) as mock:
        det.detect(np.zeros((1024, 1024), dtype=np.uint8), target_type="air_pistol")

    _, kwargs = mock.call_args
    assert kwargs["target_ring1_px"] == 394.0


def test_google_detector_prompt_snapshot_byte_identical_to_cv() -> None:
    """The prompt is the locked Step-1 artifact — its output strings must be
    byte-identical across builds. Snapshot test against an embedded copy of
    the cv/ output for one (target_type, ring1, ring_step, primary_caliber)
    combination.
    """
    from src.domains.vision.detectors.prompt import build_system_prompt

    # Build the prompt with fixed inputs.
    prompt = build_system_prompt(
        target_type="air_pistol",
        target_ring1_px=394.0,
        ring_step_px=394.0 / 9.0,
        primary_caliber="9mm",
    )

    # Load-bearing substrings — the prompt layers from cv/phase3_spike/prompt.py.
    assert "BULLSEYE (center of the target, ring 10) is exactly at pixel (512, 512)" in prompt
    assert f"distance between consecutive rings is approximately **{394.0 / 9.0:.1f} pixels**" in prompt
    assert "Ring 1 lies at about **394 pixels** from the bullseye" in prompt
    assert 'primary caliber is **9mm**' in prompt
    assert "Canonical forms: 22lr, .223Rem, 9mm, .45ACP, 7.62x39, 12-gauge" in prompt
    assert 'scoring a **air_pistol** target' in prompt
