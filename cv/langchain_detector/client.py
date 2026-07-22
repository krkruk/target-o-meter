"""VLM client for the live pipeline (Phase 3 Step 2).

Thin extension of the Step-1 spike client (``cv.phase3_spike.client``). Adds
``analyze_array()`` so the pipeline — which holds the 1024×1024 image as an
in-memory array — can call the model without writing a temp file. The existing
``analyze(path)`` is unchanged on the spike; here we override it to delegate to
``analyze_array`` (handoff subtlety #2).

Everything else (model-agnostic Google AI Studio wiring, structured output via
``with_structured_output(TargetAnalysis)``, GOOGLE_API_KEY env read) is reused
verbatim from the spike.
"""
from __future__ import annotations

import base64
import time
from pathlib import Path

import cv2
import numpy as np
from langchain_core.messages import HumanMessage, SystemMessage

from cv.detector_base import TargetType
from cv.phase3_spike.client import VLMSpikeClient
from cv.phase3_spike.prompt import build_system_prompt, build_user_text
from cv.phase3_spike.schema import TargetAnalysis


def _encode_image_b64(image: np.ndarray) -> bytes:
    """PNG-encode a uint8 H×W (or H×W×C) array and base64-encode it."""
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("cv2.imencode failed for the LLM input image")
    return base64.b64encode(buf.tobytes())


class PipelineVLMClient(VLMSpikeClient):
    """Spike client + ``analyze_array`` for the live pipeline.

    ``analyze(path)`` is overridden to read + decode the file into an array,
    then delegate to ``analyze_array`` — single code path, no duplication.
    """

    def analyze_array(
        self,
        image: np.ndarray,
        target_type: TargetType,
        target_ring1_px: float,
        ring_step_px: float | None = None,
        primary_caliber: str | None = None,
    ) -> tuple[TargetAnalysis, dict]:
        """Run one structured-output analysis on an in-memory 1024×1024 image.

        The image is PNG-encoded and base64-wrapped directly (no temp file) —
        this is the path the live pipeline uses, since it holds the normalized
        image as an array.
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
            "input_path": None,  # array path
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

        Single code path: the only difference between the path- and array-based
        entry points is how the pixels enter memory.
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
