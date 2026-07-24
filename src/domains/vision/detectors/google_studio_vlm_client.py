"""Google AI Studio VLM client binding for ``VLMClient``.

Sister module to ``google_ai_studio_detector.py`` — split per the
one-class-per-file rule (``lessons.md``). Reads ``GOOGLE_API_KEY`` from the
environment (the user exports it via ``~/.bashrc``; ``.env`` loaded by the
CLI is also fine). Raises a clear ``RuntimeError`` if absent.

Ported verbatim from ``cv/langchain_detector/client.py`` (spike side).
"""
from __future__ import annotations

import os

from langchain_google_genai import ChatGoogleGenerativeAI

from src.domains.vision.detectors.schema import TargetAnalysis
from src.domains.vision.detectors.vlm_client import VLMClient


# Locked in Step 1 (mean Jaccard 0.799). cv/langchain_detector/detector.py:26.
DEFAULT_MODEL = "gemini-3.5-flash-lite"


class GoogleStudioVLMClient(VLMClient):
    """Google AI Studio binding for ``VLMClient``.

    Constructs ``ChatGoogleGenerativeAI(model=...).with_structured_output(
    TargetAnalysis)``.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        temperature: float = 1.0,
    ) -> None:
        if not os.environ.get("GOOGLE_API_KEY"):
            raise RuntimeError(
                "GOOGLE_API_KEY is not set in the environment. "
                "Export it (e.g. via `export GOOGLE_API_KEY=...` or a .env file) "
                "before constructing GoogleAIStudioDetector."
            )
        self.model = model
        self.temperature = temperature
        self._llm = ChatGoogleGenerativeAI(
            model=model,
            temperature=temperature,
            max_output_tokens=4096,
        )
        self._structured = self._llm.with_structured_output(TargetAnalysis)
