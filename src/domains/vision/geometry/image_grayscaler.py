"""Stage-1 grayscale primitive — ``ImageGrayscaler``.

Ported from ``cv/blob_detect.py:41-42`` (``to_gray`` verbatim).

Per the one-class-per-file rule (``lessons.md``) this module also hosts the
``_sobel_mag`` gradient-magnitude helper because it is a small grayscale-output
primitive shared by ``BlackDiscCalibrator`` (``black_disc_calibrator.py``) and
``TargetLocalizer`` (``target_localizer.py``). Both consume the same uint8
gradient-magnitude image; co-locating it with ``ImageGrayscaler`` keeps a
single owner for grayscale-conversion concerns.
"""
from __future__ import annotations

import cv2
import numpy as np


def _sobel_mag(gray: np.ndarray) -> np.ndarray:
    """Sobel magnitude (0..255 uint8) — ported verbatim from cv/blob_detect.py:158-163."""
    blur = cv2.GaussianBlur(gray, (0, 0), 2.0)
    gx = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    return ((mag / max(mag.max(), 1e-6)) * 255).astype(np.uint8)


class ImageGrayscaler:
    """Stage-1 grayscale conversion — ``cv/blob_detect.py:41-42`` verbatim."""

    @staticmethod
    def to_gray(bgr: np.ndarray) -> np.ndarray:
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
