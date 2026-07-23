"""EXIF-aware image intake — the pipeline's stage 1.

Ported verbatim from ``cv/gt.py::load_bgr`` (commit 76f6fc4). Only the load
helper is ported; the magenta GT helpers (eval-only) belong in ``eval/``.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps


class ImageLoader:
    """Load an image EXIF-normalised to upright orientation, as BGR uint8."""

    @staticmethod
    def load_bgr(path: str | Path) -> np.ndarray:
        pil = Image.open(path)
        pil = ImageOps.exif_transpose(pil)
        rgb = np.array(pil.convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
