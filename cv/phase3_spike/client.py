"""LangChain client for Gemma 4 31B-it on Google AI Studio (Phase 3 spike).

Thin wrapper around ``ChatGoogleGenerativeAI.with_structured_output``. No
fallback (Q7: option c). Single shot per image at temperature 1.0 (Q9: option
b, to avoid AI Studio throttling on repeated calls).

The client is intentionally not a ``HoleDetector`` subclass yet — Step 1 is a
standalone harness decoupled from the fused pipeline (Q1). Step 2 will adapt
this into ``cv/langchain_detector/`` behind the seam.
"""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from cv.detector_base import TargetType
from cv.phase3_spike.prompt import build_system_prompt, build_user_text
from cv.phase3_spike.schema import TargetAnalysis

_DEFAULT_MODEL = "gemma-4-31b-it"
_DEFAULT_VISION_TOKEN_BUDGET = 1120  # Q2 default (max detail)


class VLMSpikeClient:
    """Calls a Google AI Studio vision model with structured output.

    Model-agnostic: serves Gemma 4 31B-it (open-weights, default) and any
    Gemini Flash variant on the same key. Reads ``GOOGLE_API_KEY`` from the
    environment (exported in the user's shell via ~/.bashrc; .env not required).

    Note: the per-image vision-token budget kwarg only applies to Gemma 4 /
    Gemini 2.x family models that expose it; Gemini 3.x Flash ignores it (uses
    its own adaptive tokenization), so it is not passed by default.
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        temperature: float = 1.0,
        vision_token_budget: int | None = None,
    ) -> None:
        if not os.environ.get("GOOGLE_API_KEY"):
            raise RuntimeError(
                "GOOGLE_API_KEY is not set in the environment. "
                "Export it (e.g. in ~/.bashrc) before running the spike."
            )
        self.model = model
        self.temperature = temperature
        kwargs: dict = dict(model=model, temperature=temperature, max_output_tokens=4096)
        # Only Gemma 4 / Gemini 2.x expose the per-image vision-token budget.
        if vision_token_budget is not None:
            kwargs["vision_token_budget"] = vision_token_budget
        self._llm = ChatGoogleGenerativeAI(**kwargs)
        self._structured = self._llm.with_structured_output(TargetAnalysis)

    def analyze(
        self,
        image_path: Path,
        target_type: TargetType,
        target_ring1_px: float,
        ring_step_px: float | None = None,
        primary_caliber: str | None = None,
    ) -> tuple[TargetAnalysis, dict]:
        """Run one structured-output analysis on a 1024x1024 LLM-input image.

        Returns (parsed TargetAnalysis, timing/usage meta dict).
        """
        sys = SystemMessage(content=build_system_prompt(
            target_type=target_type,
            target_ring1_px=target_ring1_px,
            ring_step_px=ring_step_px,
            primary_caliber=primary_caliber,
        ))
        b64 = base64.b64encode(image_path.read_bytes()).decode()
        human = HumanMessage(content=[
            {"type": "text", "text": build_user_text()},
            {"type": "image_url", "image_url": f"data:image/png;base64,{b64}"},
        ])
        t0 = time.time()
        result = self._structured.invoke([sys, human])
        elapsed = time.time() - t0
        meta = {
            "model": self.model,
            "temperature": self.temperature,
            "elapsed_s": round(elapsed, 2),
            "n_holes": len(result.holes),
        }
        return result, meta
