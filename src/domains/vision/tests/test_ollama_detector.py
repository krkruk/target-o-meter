"""OllamaDetector unit tests — mocked LangChain invoke (no network).

Verifies env override of ``OLLAMA_MODEL`` / ``OLLAMA_HOST``, the ``name``
property, and that the Ollama-specific ``raw["served_by"]`` field is set.
"""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from src.domains.vision.detectors.detection_result import DetectionResult
from src.domains.vision.detectors.ollama_detector import OllamaDetector
from src.domains.vision.detectors.schema import Hole, TargetAnalysis


def _mock_target_analysis() -> TargetAnalysis:
    return TargetAnalysis(
        holes=[
            Hole(x=512, y=512, score=10, confidence=0.95, caliber="9mm"),
        ],
        target_type="air_pistol",
        notes=None,
    )


def test_ollama_detector_defaults_model_and_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    det = OllamaDetector()
    assert det.name == "ollama-gemma4:latest"
    assert det._host == "http://localhost:11434"


def test_ollama_detector_respects_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2:latest")
    monkeypatch.setenv("OLLAMA_HOST", "http://gpu-box:11434")
    det = OllamaDetector()
    assert det.name == "ollama-llama3.2:latest"
    assert det._host == "http://gpu-box:11434"


def test_ollama_detector_explicit_args_override_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "env-model")
    det = OllamaDetector(model="explicit-model", host="http://explicit:11434")
    assert det.name == "ollama-explicit-model"
    assert det._host == "http://explicit:11434"


def test_ollama_detector_detect_maps_and_adds_served_by(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    det = OllamaDetector(model="gemma4:latest", host="http://localhost:11434")

    fake_meta = {"model": "gemma4:latest", "temperature": 1.0, "elapsed_s": 1.2, "n_holes": 1, "input_path": None}
    with patch.object(det._client, "analyze_array", return_value=(_mock_target_analysis(), fake_meta)):
        result = det.detect(
            image_1024=np.zeros((1024, 1024), dtype=np.uint8),
            target_type="air_pistol",
            target_ring1_px=394.0,
        )

    assert isinstance(result, DetectionResult)
    assert result.detector_name == "ollama-gemma4:latest"
    assert len(result.holes) == 1
    assert result.holes[0].score == 10
    # Ollama-specific: surfaces the local serving endpoint so consumers can
    # distinguish cloud vs local even when model names overlap.
    assert result.raw["served_by"] == "ollama@http://localhost:11434"
    assert result.raw["model"] == "gemma4:latest"
    assert result.raw["target_ring1_px"] == 394.0
