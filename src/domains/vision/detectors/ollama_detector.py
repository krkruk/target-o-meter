"""Ollama ``OllamaDetector`` strategy (NEW — the never-before-built peer strategy).

Backed by ``langchain_ollama.ChatOllama``; default model ``gemma4:latest``
(env-configurable via ``OLLAMA_MODEL``). Same schema + same prompt + same
``analyze_array`` path as the Google detector — it is a true peer, not a
fallback.

Reads ``OLLAMA_HOST`` (default ``http://localhost:11434``) and ``OLLAMA_MODEL``
(default ``gemma4:latest``) from env. The VLM client binding lives in
``ollama_vlm_client.py`` per the one-class-per-file rule (``lessons.md``).
"""
from __future__ import annotations

import os
from typing import Optional

import numpy as np

from src.domains.vision.detectors.detection_result import DetectionResult
from src.domains.vision.detectors.google_ai_studio_detector import (
    analysis_to_detection_result,
)
from src.domains.vision.detectors.ollama_vlm_client import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    OllamaVLMClient,
)
from src.domains.vision.ports import HoleDetector, TargetType


class OllamaDetector(HoleDetector):
    """HoleDetector strategy backed by a local Ollama VLM via LangChain.

    Defaults model + host from env (``OLLAMA_MODEL`` / ``OLLAMA_HOST``), falling
    back to documented defaults. The ``detect()`` mapping is identical to the
    Google detector's — both share ``analysis_to_detection_result``.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        host: Optional[str] = None,
        temperature: float = 1.0,
    ) -> None:
        # Read env at construction time (the CLI loads .env before this runs).
        model = model or os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
        host = host or os.environ.get("OLLAMA_HOST", DEFAULT_HOST)
        self._client = OllamaVLMClient(model=model, host=host, temperature=temperature)
        self._model = model
        self._host = host

    @property
    def name(self) -> str:
        return f"ollama-{self._model}"

    def detect(
        self,
        image_1024: np.ndarray,
        target_type: TargetType,
        caliber_hint: Optional[str] = None,
        target_ring1_px: Optional[float] = None,
    ) -> DetectionResult:
        if target_ring1_px is None or target_ring1_px <= 0:
            target_ring1_px = 394.0
        ring_step_px = float(target_ring1_px) / 9.0

        analysis, meta = self._client.analyze_array(
            image=image_1024,
            target_type=target_type,
            target_ring1_px=target_ring1_px,
            ring_step_px=ring_step_px,
            primary_caliber=caliber_hint,
        )

        result = analysis_to_detection_result(
            analysis=analysis,
            detector_name=self.name,
            target_ring1_px=target_ring1_px,
            ring_step_px=ring_step_px,
            primary_caliber=caliber_hint,
            meta=meta,
        )
        # Surface the serving path so consumers can tell local-Ollama apart
        # from cloud-Google even when the model name is similar.
        result.raw = dict(result.raw or {})
        result.raw["served_by"] = f"ollama@{self._host}"
        return result
