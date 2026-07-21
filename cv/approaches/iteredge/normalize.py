"""Normalize warped image to 1024x1024 with bullseye at (512,512), 1-ring at r=500.

Forward chain (source → 1024):
  1. crop:   src[y0:y0+h, x0:x0+w]
  2. warp:   p_warped ~ H_full @ p_crop        (homography, includes the
             recentering translation that puts bullseye at (half, half))
  3. resize: p_1024 = p_warped * scale + (tx, ty)

Inverse chain (1024 → source), exact analytical inverse:
  1. un-resize: p_warped = (p_1024 - (tx, ty)) / scale
  2. un-warp:   p_crop ~ H_full^-1 @ p_warped
  3. un-crop:   p_src = p_crop + (x0, y0)

Because the inverse is exact and analytical, the bullseye round-trip error
should be < 1e-6 px (just float64 arithmetic).
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class IterEdgeTransformMeta:
    """All parameters needed to invert the forward transform chain."""
    bbox: tuple[int, int, int, int]
    H_full: np.ndarray            # full homography: crop → warped
    H_full_inv: np.ndarray        # inverse: warped → crop
    out_size_warped: tuple[int, int]   # (w, h) of the warped image
    bullseye_warped: tuple[float, float]  # where bullseye lands in warped frame
    scale: float
    tx: float
    ty: float
    size: int                     # 1024
    r_ring1_warped: float
    cx_crop: float
    cy_crop: float


def normalize_to_1024(
    warped: np.ndarray,
    H_full: np.ndarray,
    bullseye_warped: tuple[float, float],
    bbox: tuple[int, int, int, int],
    r_ring1_warped: float,
    cx_crop: float,
    cy_crop: float,
    target_ring1_px: float = 500.0,
    size: int = 1024,
    fill_value: int = 245,
) -> tuple[np.ndarray, IterEdgeTransformMeta]:
    """Resize + pad the warped image to 1024×1024 with bullseye at centre."""
    h, w = warped.shape[:2]
    if r_ring1_warped <= 0:
        r_ring1_warped = float(max(h, w)) / 2.0
    scale = float(target_ring1_px) / r_ring1_warped

    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = cv2.resize(warped, (new_w, new_h), interpolation=cv2.INTER_AREA)

    bcx, bcy = bullseye_warped
    bullseye_resized_x = bcx * scale
    bullseye_resized_y = bcy * scale

    target_cx = size / 2.0
    target_cy = size / 2.0
    tx = target_cx - bullseye_resized_x
    ty = target_cy - bullseye_resized_y

    canvas = np.full((size, size), fill_value, dtype=np.uint8)
    dst_x0 = int(round(tx)); dst_y0 = int(round(ty))
    src_x0 = max(0, -dst_x0); src_y0 = max(0, -dst_y0)
    src_x1 = min(new_w, size - dst_x0); src_y1 = min(new_h, size - dst_y0)
    out_x0 = max(0, dst_x0); out_y0 = max(0, dst_y0)
    out_x1 = min(size, dst_x0 + new_w); out_y1 = min(size, dst_y0 + new_h)
    if out_x1 > out_x0 and out_y1 > out_y0 and src_x1 > src_x0 and src_y1 > src_y0:
        canvas[out_y0:out_y1, out_x0:out_x1] = resized[src_y0:src_y1, src_x0:src_x1]

    H_full_inv = np.linalg.inv(H_full)
    meta = IterEdgeTransformMeta(
        bbox=tuple(int(v) for v in bbox),
        H_full=H_full,
        H_full_inv=H_full_inv,
        out_size_warped=(int(w), int(h)),
        bullseye_warped=(float(bcx), float(bcy)),
        scale=float(scale), tx=float(tx), ty=float(ty),
        size=int(size), r_ring1_warped=float(r_ring1_warped),
        cx_crop=float(cx_crop), cy_crop=float(cy_crop),
    )
    return canvas, meta


# ---------------------------------------------------------------------------
# Inverse transforms
# ---------------------------------------------------------------------------
def _apply_H_inverse(pts: np.ndarray, H_inv: np.ndarray) -> np.ndarray:
    """Apply H_inv to Nx2 points (dehomogenize)."""
    pts = np.asarray(pts, dtype=np.float64)
    if pts.size == 0:
        return pts.reshape(0, 2)
    homog = np.concatenate([pts, np.ones((pts.shape[0], 1))], axis=1)
    out = homog @ H_inv.T
    w = out[:, 2:3]
    w = np.where(np.abs(w) < 1e-12, 1e-12, w)
    return out[:, :2] / w


def norm_to_warped(x: float, y: float, meta: IterEdgeTransformMeta) -> tuple[float, float]:
    return ((x - meta.tx) / meta.scale, (y - meta.ty) / meta.scale)


def warped_to_crop(x: float, y: float, meta: IterEdgeTransformMeta) -> tuple[float, float]:
    p = _apply_H_inverse(np.array([[x, y]], dtype=np.float64), meta.H_full_inv)[0]
    return float(p[0]), float(p[1])


def crop_to_source_xy(x: float, y: float, meta: IterEdgeTransformMeta) -> tuple[float, float]:
    return float(x + meta.bbox[0]), float(y + meta.bbox[1])


def norm_to_crop(x: float, y: float, meta: IterEdgeTransformMeta) -> tuple[float, float]:
    wx, wy = norm_to_warped(x, y, meta)
    return warped_to_crop(wx, wy, meta)


def norm_to_source(x: float, y: float, meta: IterEdgeTransformMeta) -> tuple[float, float]:
    cx, cy = norm_to_crop(x, y, meta)
    return crop_to_source_xy(cx, cy, meta)


def self_test_inversion(meta: IterEdgeTransformMeta) -> float:
    """Round-trip the crop-frame bullseye (cx_crop, cy_crop) → 1024 → crop.
    Returns the recovery error in crop px (should be < 1e-6)."""
    cx_c, cy_c = meta.cx_crop, meta.cy_crop
    # Forward to warped.
    homog = np.array([cx_c, cy_c, 1.0], dtype=np.float64)
    p_w = meta.H_full @ homog
    p_w = p_w[:2] / p_w[2]
    # Forward to 1024.
    p_1024 = p_w * meta.scale + np.array([meta.tx, meta.ty])
    # Inverse back to crop.
    p_back_warped = (p_1024 - np.array([meta.tx, meta.ty])) / meta.scale
    p_back_crop = _apply_H_inverse(p_back_warped.reshape(1, 2), meta.H_full_inv)[0]
    return float(np.hypot(p_back_crop[0] - cx_c, p_back_crop[1] - cy_c))
