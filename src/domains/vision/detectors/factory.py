"""Detector construction by name — shared by the CLI and (later) the BFF.

Honors the "explicit choice, no failover" decision (research § architecture
decision): each name maps to one detector class; unknown names raise.
"""
from __future__ import annotations

from src.domains.vision.detectors.google_ai_studio_detector import GoogleAIStudioDetector
from src.domains.vision.detectors.mock_detector import MockDetector
from src.domains.vision.detectors.ollama_detector import OllamaDetector
from src.domains.vision.ports import HoleDetector


class DetectorFactory:
    """Build a ``HoleDetector`` by short name.

    Supported names (per plan §6.1):
      - ``"google"`` → ``GoogleAIStudioDetector`` (reads ``GOOGLE_API_KEY``)
      - ``"ollama"`` → ``OllamaDetector`` (reads ``OLLAMA_HOST`` / ``OLLAMA_MODEL``)
      - ``"mock"`` → ``MockDetector`` (no API calls; plumbing / regression tests)

    Env defaults are read inside each detector, not the factory. Raises
    ``ValueError`` on unknown names.
    """

    @staticmethod
    def build(name: str, **kwargs) -> HoleDetector:
        if name == "google":
            return GoogleAIStudioDetector(**kwargs)
        if name == "ollama":
            return OllamaDetector(**kwargs)
        if name == "mock":
            return MockDetector()
        raise ValueError(
            f"unknown detector name: {name!r}. "
            f"Supported: 'google', 'ollama', 'mock'."
        )
