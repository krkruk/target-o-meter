"""Apply the optimized homography via cv2.warpPerspective.

Decides the output frame size automatically from the source crop size + H,
so the bullseye lands at the centre of a clean rectangular image and the
1-ring outer is well within the frame.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from cv.approaches.iteredge.model import params_to_H


def compute_output_shape(H: np.ndarray, src_shape: tuple[int, int], cx: float, cy: float,
                          r_ring1: float, margin_factor: float = 1.25) -> tuple[int, int, np.ndarray]:
    """Compute output (w, h, H_with_translation) so the bullseye lands at the
    centre of the output frame.

    The output frame is sized so that margin_factor × r_ring1 fits in both
    dimensions. We do NOT modify H other than composing with a translation
    that re-centres the bullseye.

    Returns (out_w, out_h, H_full) where H_full is the homography to pass to
    cv2.warpPerspective (maps src→output).
    """
    half = int(math.ceil(margin_factor * r_ring1))
    out_w = 2 * half
    out_h = 2 * half

    # Find where the bullseye lands in the output under H alone.
    src_bull = np.array([[cx, cy]], dtype=np.float64)
    from cv.approaches.iteredge.model import apply_H_to_points
    bull_out = apply_H_to_points(H, src_bull)[0]
    # Compose with translation so bullseye lands at (half, half).
    dx = half - bull_out[0]
    dy = half - bull_out[1]
    T = np.array([
        [1.0, 0.0, dx],
        [0.0, 1.0, dy],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)
    H_full = T @ H
    return out_w, out_h, H_full


def apply_warp(src: np.ndarray, H_full: np.ndarray, out_size: tuple[int, int],
               border_value: float = 245.0) -> np.ndarray:
    """Apply the homography. Works on grayscale (uint8) or BGR."""
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
            src, (H_full), (out_w, out_h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=border_color,
        )
