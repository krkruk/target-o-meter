"""Apply the refined homography via ``cv2.warpPerspective``.

Ported verbatim from ``cv/approaches/iteredge/warp.py`` (67 LOC at commit
76f6fc4). Decides the output frame size automatically from source crop + H
so the bullseye lands at the centre of a clean rectangular image and the
1-ring outer is well within the frame.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from src.domains.vision.geometry.homography_model import HomographyModel


class WarpProjector:
    """Apply the optimized homography. Ported from iteredge/warp.py."""

    @staticmethod
    def compute_output_shape(
        H: np.ndarray, src_shape: tuple[int, int], cx: float, cy: float,
        r_ring1: float, margin_factor: float = 1.25,
    ) -> tuple[int, int, np.ndarray]:
        """Compute output ``(w, h, H_with_translation)`` so the bullseye lands
        at the centre of the output frame.

        Ported verbatim from cv/approaches/iteredge/warp.py:17-46.
        """
        half = int(math.ceil(margin_factor * r_ring1))
        out_w = 2 * half
        out_h = 2 * half

        src_bull = np.array([[cx, cy]], dtype=np.float64)
        bull_out = HomographyModel.apply_H_to_points(H, src_bull)[0]
        dx = half - bull_out[0]
        dy = half - bull_out[1]
        T = np.array([
            [1.0, 0.0, dx],
            [0.0, 1.0, dy],
            [0.0, 0.0, 1.0],
        ], dtype=np.float64)
        H_full = T @ H
        return out_w, out_h, H_full

    @staticmethod
    def apply_warp(
        src: np.ndarray, H_full: np.ndarray, out_size: tuple[int, int],
        border_value: float = 245.0,
    ) -> np.ndarray:
        """Apply the homography. Works on grayscale (uint8) or BGR.
        Ported verbatim from cv/approaches/iteredge/warp.py:49-67.
        """
        out_w, out_h = out_size
        if src.ndim == 2:
            return cv2.warpPerspective(
                src, H_full, (out_w, out_h),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=border_value,
            )
        else:
            border_color = (border_value, border_value, border_value)
            return cv2.warpPerspective(
                src, H_full, (out_w, out_h),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=border_color,
            )
