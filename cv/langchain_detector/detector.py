"""LangChainDetector — the Phase 3 Step 2 HoleDetector strategy.

Plugs the locked Step-1 LLM detector (``gemini-3.5-flash-lite``) behind the
existing ``HoleDetector`` seam. The fused/full pipeline calls ``detect()`` with
the 1024×1024 normalized image + the ring geometry; this strategy runs the VLM
with structured output and maps the parsed ``TargetAnalysis`` to a
``DetectionResult``.

Geometry never changes — only the detector call. See the handoff doc
(research-ai-detection.md § Phase 3 Step 2 handoff).
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from cv.detector_base import (
    DetectionResult,
    DetectedHole,
    HoleDetector,
    TargetType,
)
from cv.langchain_detector.client import PipelineVLMClient

_DEFAULT_MODEL = "gemini-3.5-flash-lite"  # locked in Step 1 (mean Jaccard 0.799)


class LangChainDetector(HoleDetector):
    """HoleDetector strategy backed by a Google AI Studio VLM via LangChain.

    The model is fixed per instance (constructed once in run.py, reused across
    images). ``GOOGLE_API_KEY`` must be in the environment (user exports it via
    ~/.bashrc; see AGENTS.md).
    """

    def __init__(self, model: str = _DEFAULT_MODEL) -> None:
        self._client = PipelineVLMClient(model=model)
        self._model = model

    @property
    def name(self) -> str:
        return f"langchain-{self._model}"

    def detect(
        self,
        image_1024: np.ndarray,
        target_type: TargetType,
        caliber_hint: Optional[str] = None,
        target_ring1_px: Optional[float] = None,
    ) -> DetectionResult:
        # The prompt + magenta-dot sizing both need target_ring1_px (handoff
        # subtlety #1). Fall back to the Phase-2.5 default observed radius if
        # somehow absent — the prompt still works qualitatively.
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

        holes = [
            DetectedHole(
                x=int(h.x),
                y=int(h.y),
                score=int(h.score),
                confidence=float(h.confidence),
                caliber=h.caliber,
            )
            for h in analysis.holes
        ]

        return DetectionResult(
            holes=holes,
            target_type=analysis.target_type,
            detector_name=self.name,
            notes=analysis.notes,
            raw={
                "model": self._model,
                "target_ring1_px": float(target_ring1_px),
                "ring_step_px": ring_step_px,
                "primary_caliber_hint": caliber_hint,
                "calibers": [h.caliber for h in analysis.holes],
                **meta,
            },
        )
