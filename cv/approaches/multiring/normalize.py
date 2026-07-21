"""Normalize the warped image to a 1024x1024 square + inverse helpers.

Forward chain (source-image -> 1024-normalized):
  1. crop:         gray_src[y0:y0+h, x0:x0+w]                      via localize
  2. warp:         p_warped = H_eff @ [p_crop, 1] / w               via warp
  3. resize+pad:   p_1024   = (p_warped - bullseye) · scale + (512, 512) + canvas pad

Inverse chain (1024 -> source):
  1. un-pad+resize: p_warped = (p_1024 - (512, 512)) / scale + bullseye
  2. un-warp:       p_crop   = H_eff^{-1} @ [p_warped, 1]
  3. un-crop:       p_src    = p_crop + (x0, y0)

Layout: bullseye at (512, 512); calibrated 1-ring outer boundary at radius 500 px.
Ring step in 1024-px units is 500/9 ≈ 55.5 px.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class TransformMeta:
    """All parameters needed to invert the forward transform chain."""
    bbox: tuple[int, int, int, int]                  # (x0, y0, w, h) in source-image px
    H_eff: np.ndarray                                # 3x3 projective (warp + translation)
    H_eff_inv: np.ndarray                            # 3x3 inverse of H_eff
    bullseye_warped: tuple[float, float]             # (cx, cy) in warped px
    r_ring1_warped: float                            # 1-ring outer radius in warped px
    scale: float                                     # 500 / r_ring1_warped
    size: int                                        # 1024
    fill_value: int                                  # 245


def to_llm_square(
    warped: np.ndarray,
    warp_meta: dict,
    bbox: tuple[int, int, int, int],
    target_ring1_px: float = 500.0,
    size: int = 1024,
    fill_value: int = 245,
) -> tuple[np.ndarray, TransformMeta]:
    """Resize + pad the warped image so the bullseye lands at (size/2, size/2)
    and the calibrated 1-ring outer lands at radius target_ring1_px.

    Uses a single scale + translation (no rotation/scaling anisotropy) so the
    inversion is exact to floating-point precision.
    """
    h, w = warped.shape[:2]
    r_ring1_warped = float(warp_meta["r_ring1_warped"])
    if r_ring1_warped <= 0:
        r_ring1_warped = float(max(h, w)) / 2.2
    scale = float(target_ring1_px) / r_ring1_warped

    # Resize the warped image (INTER_AREA for downsample, INTER_CUBIC for up).
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = cv2.resize(warped, (new_w, new_h), interpolation=interp)

    cx_w, cy_w = warp_meta["bullseye_warped"]
    bullseye_resized = (cx_w * scale, cy_w * scale)

    target_cx = size / 2.0
    target_cy = size / 2.0
    tx = target_cx - bullseye_resized[0]
    ty = target_cy - bullseye_resized[1]

    canvas = np.full((size, size), fill_value, dtype=np.uint8)
    dst_x0 = int(round(tx))
    dst_y0 = int(round(ty))
    src_x0 = max(0, -dst_x0)
    src_y0 = max(0, -dst_y0)
    src_x1 = min(new_w, size - dst_x0)
    src_y1 = min(new_h, size - dst_y0)
    out_x0 = max(0, dst_x0)
    out_y0 = max(0, dst_y0)
    out_x1 = min(size, dst_x0 + new_w)
    out_y1 = min(size, dst_y0 + new_h)
    if out_x1 > out_x0 and out_y1 > out_y0 and src_x1 > src_x0 and src_y1 > src_y0:
        canvas[out_y0:out_y1, out_x0:out_x1] = resized[src_y0:src_y1, src_x0:src_x1]

    meta = TransformMeta(
        bbox=tuple(int(v) for v in bbox),
        H_eff=warp_meta["H_eff"],
        H_eff_inv=warp_meta["H_eff_inv"],
        bullseye_warped=warp_meta["bullseye_warped"],
        r_ring1_warped=r_ring1_warped,
        scale=scale,
        size=int(size),
        fill_value=int(fill_value),
    )
    return canvas, meta


# ---------------------------------------------------------------------------
# Inverse transforms
# ---------------------------------------------------------------------------
def norm_to_crop(xy_norm: tuple[float, float], meta: TransformMeta) -> tuple[float, float]:
    """1024 coords → crop coords (inverts to_llm_square + warp)."""
    # Un-do resize + pad: get warped-frame px.
    target_cx = meta.size / 2.0
    target_cy = meta.size / 2.0
    p_warped = (np.array([float(xy_norm[0]) - target_cx, float(xy_norm[1]) - target_cy], dtype=np.float64)
                / meta.scale)
    p_warped += np.array(meta.bullseye_warped, dtype=np.float64)
    # Un-do projective warp.
    v = meta.H_eff_inv @ np.array([p_warped[0], p_warped[1], 1.0], dtype=np.float64)
    if abs(v[2]) < 1e-12:
        v[2] = 1e-12
    return float(v[0] / v[2]), float(v[1] / v[2])


def crop_to_source(xy_crop: tuple[float, float], meta: TransformMeta) -> tuple[float, float]:
    """Crop coords → source-image coords."""
    return float(xy_crop[0] + meta.bbox[0]), float(xy_crop[1] + meta.bbox[1])


def norm_to_source(xy_norm: tuple[float, float], meta: TransformMeta) -> tuple[float, float]:
    """Full inverse: 1024 coords → source-image coords."""
    return crop_to_source(norm_to_crop(xy_norm, meta), meta)


def self_test_inversion(meta: TransformMeta, rings: list[dict]) -> float:
    """Round-trip the mean ring center (bullseye in crop frame) through the
    forward transforms and back; return the recovery error in crop-px.

    Must be < 0.01 px (floating-point precision).
    """
    import math
    if not rings:
        return float("inf")
    # Weighted mean of ring centers (matches warp.py's bullseye prior).
    weights = np.array([1.0 / max(math.sqrt(r["semi_a"] * r["semi_b"]), 1.0) for r in rings])
    weights /= weights.sum()
    cx_crop = float(sum(w * r["cx"] for w, r in zip(weights, rings)))
    cy_crop = float(sum(w * r["cy"] for w, r in zip(weights, rings)))

    # Forward: crop → warped.
    v = meta.H_eff @ np.array([cx_crop, cy_crop, 1.0], dtype=np.float64)
    if abs(v[2]) < 1e-12:
        return float("inf")
    p_warped = v[:2] / v[2]
    # Forward: warped → 1024.
    target_cx = meta.size / 2.0
    target_cy = meta.size / 2.0
    p_norm = (p_warped - np.array(meta.bullseye_warped, dtype=np.float64)) * meta.scale
    p_norm += np.array([target_cx, target_cy], dtype=np.float64)

    # Inverse: 1024 → crop.
    xy_back = norm_to_crop((float(p_norm[0]), float(p_norm[1])), meta)
    return float(math.hypot(xy_back[0] - cx_crop, xy_back[1] - cy_crop))
