"""Google AI Studio ``GoogleAIStudioDetector`` strategy.

Ported verbatim from ``cv/langchain_detector/detector.py`` (91 LOC at commit
76f6fc4). Backed by ``langchain_google_genai.ChatGoogleGenerativeAI``; locked
model ``gemini-3.5-flash-lite`` (the Step-1 result that hit mean Jaccard
0.799).

The VLM client binding lives in ``google_studio_vlm_client.py`` per the
one-class-per-file rule (``lessons.md``).
"""
from __future__ import annotations

from src.domains.vision.detectors.detection_result import DetectionResult
from src.domains.vision.detectors.detected_hole import DetectedHole
from src.domains.vision.detectors.google_studio_vlm_client import (
    DEFAULT_MODEL,
    GoogleStudioVLMClient,
)
from src.domains.vision.detectors.schema import TargetAnalysis
from src.domains.vision.ports import HoleDetector, TargetType


class GoogleAIStudioDetector(HoleDetector):
    """HoleDetector strategy backed by a Google AI Studio VLM via LangChain.

    The model is fixed per instance (constructed once, reused across images).
    ``GOOGLE_API_KEY`` must be in the environment.

    Ported from cv/langchain_detector/detector.py:29-91 (renamed from
    ``LangChainDetector`` to reflect the binding).
    """

    def __init__(self, model: str = DEFAULT_MODEL, temperature: float = 1.0) -> None:
        self._client = GoogleStudioVLMClient(model=model, temperature=temperature)
        self._model = model

    @property
    def name(self) -> str:
        return f"google-{self._model}"

    def detect(
        self,
        image_1024,
        target_type: TargetType,
        caliber_hint=None,
        target_ring1_px=None,
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

        return analysis_to_detection_result(
            analysis=analysis,
            detector_name=self.name,
            target_ring1_px=target_ring1_px,
            ring_step_px=ring_step_px,
            primary_caliber=caliber_hint,
            meta=meta,
        )


def analysis_to_detection_result(
    *,
    analysis: TargetAnalysis,
    detector_name: str,
    target_ring1_px: float,
    ring_step_px: float,
    primary_caliber,
    meta: dict,
) -> DetectionResult:
    """``TargetAnalysis`` → ``DetectionResult`` mapping — identical for the
    Google and Ollama detectors (true peers share the schema + mapping).

    Ported verbatim from cv/langchain_detector/detector.py:67-91. Public
    module function so the peer detector (``ollama_detector``) imports it
    without reaching for a ``_``-prefixed symbol across subpackages.
    """
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
        detector_name=detector_name,
        notes=analysis.notes,
        raw={
            "target_ring1_px": float(target_ring1_px),
            "ring_step_px": ring_step_px,
            "primary_caliber_hint": primary_caliber,
            "calibers": [h.caliber for h in analysis.holes],
            **meta,
        },
    )
