"""Ollama VLM client binding for ``VLMClient``.

Sister module to ``ollama_detector.py`` — split per the one-class-per-file rule
(``lessons.md``). Constructs ``ChatOllama(model=..., base_url=...)
.with_structured_output(TargetAnalysis)``. If ``with_structured_output``
behaves differently for the local model, surface the discrepancy in
``raw["served_by"]`` (set by the detector) but do not silently change the
schema.
"""
from __future__ import annotations

from langchain_ollama import ChatOllama

from src.domains.vision.detectors.schema import TargetAnalysis
from src.domains.vision.detectors.vlm_client import VLMClient


DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "gemma4:latest"


class OllamaVLMClient(VLMClient):
    """Ollama binding for ``VLMClient``."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        temperature: float = 1.0,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self._host = host
        self._llm = ChatOllama(
            model=model,
            base_url=host,
            temperature=temperature,
        )
        self._structured = self._llm.with_structured_output(TargetAnalysis)
