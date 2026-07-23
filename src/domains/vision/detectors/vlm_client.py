"""VLM client base — base64 PNG-encode of the in-memory 1024 array + message
construction.

Ported + unified from ``cv/phase3_spike/client.py`` and
``cv/langchain_detector/client.py`` (198 LOC combined at commit 76f6fc4). The
base owns the encoding and the message shape; subclasses bind a specific
LangChain chat model and expose ``self._structured``.

Both Google and Ollama subclasses share this path — they are true peers, not
fallbacks.
"""
from __future__ import annotations

import base64
import time
from pathlib import Path

import cv2
import numpy as np
from langchain_core.messages import HumanMessage, SystemMessage

from src.domains.vision.detectors.prompt import build_system_prompt, build_user_text
from src.domains.vision.detectors.schema import TargetAnalysis
from src.domains.vision.ports import TargetType


def _encode_image_b64(image: np.ndarray) -> bytes:
    """PNG-encode a uint8 H×W (or H×W×C) array and base64-encode it.
    Ported from cv/langchain_detector/client.py:29-34."""
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("cv2.imencode failed for the LLM input image")
    return base64.b64encode(buf.tobytes())


class VLMClient:
    """Abstract base for VLM clients.

    Subclasses must construct ``self._structured`` in ``__init__`` by building
    the bound chat model and calling ``.with_structured_output(TargetAnalysis)``.
    They must also set ``self.model`` and ``self.temperature`` for the meta dict.
    """

    model: str
    temperature: float
    _structured: object  # langchain Runnable

    def analyze_array(
        self,
        image: np.ndarray,
        target_type: TargetType,
        target_ring1_px: float,
        ring_step_px: float | None = None,
        primary_caliber: str | None = None,
    ) -> tuple[TargetAnalysis, dict]:
        """Run one structured-output analysis on an in-memory 1024×1024 image.

        Ported from cv/langchain_detector/client.py:44-79.
        """
        sys = SystemMessage(content=build_system_prompt(
            target_type=target_type,
            target_ring1_px=target_ring1_px,
            ring_step_px=ring_step_px,
            primary_caliber=primary_caliber,
        ))
        b64 = _encode_image_b64(image)
        human = HumanMessage(content=[
            {"type": "text", "text": build_user_text()},
            {"type": "image_url", "image_url": f"data:image/png;base64,{b64.decode()}"},
        ])
        t0 = time.time()
        result = self._structured.invoke([sys, human])
        elapsed = time.time() - t0
        meta = {
            "model": self.model,
            "temperature": self.temperature,
            "elapsed_s": round(elapsed, 2),
            "n_holes": len(result.holes),
            "input_path": None,
        }
        return result, meta

    def analyze(
        self,
        image_path: Path,
        target_type: TargetType,
        target_ring1_px: float,
        ring_step_px: float | None = None,
        primary_caliber: str | None = None,
    ) -> tuple[TargetAnalysis, dict]:
        """Read the file into an array and delegate to ``analyze_array``.

        Single code path: the only difference between path and array entry is
        how pixels enter memory. Ported from cv/langchain_detector/client.py:81-104.
        """
        image = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise FileNotFoundError(f"Could not read image: {image_path}")
        result, meta = self.analyze_array(
            image=image,
            target_type=target_type,
            target_ring1_px=target_ring1_px,
            ring_step_px=ring_step_px,
            primary_caliber=primary_caliber,
        )
        meta["input_path"] = str(image_path)
        return result, meta
