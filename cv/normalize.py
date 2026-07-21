"""Image normalization: crop -> fronto-parallel warp -> 1024x1024 square.

Forward chain (source-image -> 1024-normalized):
  1. crop:        gray_src[y0:y0+h, x0:x0+w]                  via crop_to_target
  2. warp:        p_warped = M2 @ p_crop + t                    via warp_fronto_parallel
  3. resize+pad:  p_1024  = p_warped * scale + (tx, ty)         via to_llm_square

Inverse chain (1024 -> source), applied in reverse order:
  1. un-pad+un-resize:  p_warped = (p_1024 - (tx, ty)) / scale
  2. un-warp:           p_crop   = M2_inv @ (p_warped - t)
  3. un-crop:           p_src    = p_crop + (x0, y0)

The bullseye lands at (512, 512) and the calibrated 1-ring boundary lands at
radius 500 px, so the LLM sees a fixed geometric frame regardless of source
resolution or target type. The ring step in 1024-px units is 500/9 ~ 55.5.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from cv.blob_detect import warp_fronto_parallel


@dataclass
class TransformMeta:
    """All parameters needed to invert the forward transform chain."""
    bbox: tuple[int, int, int, int]
    M2: np.ndarray
    t: np.ndarray
    out_center: tuple[float, float]
    scale: float
    tx: float
    ty: float
    size: int
    r_ring1_warped: float


def wrap_warp(crop: np.ndarray, cal: dict) -> tuple[np.ndarray, dict]:
    """Run warp_fronto_parallel and re-derive the translation vector t.

    The existing warp function returns (warped, M2, out_center) but not t.
    Since t = out_center - M2 @ (cx, cy) (per blob_detect.py:322), we can
    re-derive it from cal without modifying the existing function.
    """
    warped, M2, out_center = warp_fronto_parallel(crop, cal)
    cx, cy = cal["cx"], cal["cy"]
    t = np.asarray(out_center, dtype=np.float64) - M2 @ np.array([cx, cy], dtype=np.float64)
    return warped, {"M2": M2, "t": t, "out_center": (float(out_center[0]), float(out_center[1]))}


def to_llm_square(
    warped: np.ndarray,
    cal: dict,
    warp_meta: dict,
    bbox: tuple[int, int, int, int],
    target_ring1_px: float = 500.0,
    size: int = 1024,
    fill_value: int = 245,
) -> tuple[np.ndarray, TransformMeta]:
    """Normalize the warped image to a size x size square.

    Layout: bullseye at (size/2, size/2); calibrated 1-ring outer boundary at
    radius target_ring1_px. Outside the visible target area: fill_value (paper-
    white-ish, matches warpAffine borderValue).

    Steps:
      1. Compute the 1-ring radius in warped px: r_ring1_warped = r_bull_px + 9*s_px.
      2. scale = target_ring1_px / r_ring1_warped.
      3. Resize the warped image by scale (INTER_AREA for downsample).
      4. Translate (via canvas overlay) so the bullseye lands at (size/2, size/2).
      5. Regions of the canvas not covered by the resized image keep fill_value.
    """
    h, w = warped.shape[:2]
    r_ring1_warped = float(cal["r_bull_px"] + 9.0 * cal["s_px"])
    if r_ring1_warped <= 0.0:
        r_ring1_warped = float(max(h, w)) / 2.0
    scale = float(target_ring1_px) / r_ring1_warped

    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = cv2.resize(warped, (new_w, new_h), interpolation=cv2.INTER_AREA)

    out_cx, out_cy = warp_meta["out_center"]
    bullseye_resized_x = out_cx * scale
    bullseye_resized_y = out_cy * scale

    target_cx = size / 2.0
    target_cy = size / 2.0
    tx = target_cx - bullseye_resized_x
    ty = target_cy - bullseye_resized_y

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
        M2=warp_meta["M2"],
        t=warp_meta["t"],
        out_center=warp_meta["out_center"],
        scale=scale,
        tx=float(tx),
        ty=float(ty),
        size=int(size),
        r_ring1_warped=r_ring1_warped,
    )
    return canvas, meta


def norm_to_crop(xy_norm: tuple[float, float], meta: TransformMeta) -> tuple[float, float]:
    """Invert to_llm_square + warp_fronto_parallel: 1024 coords -> crop coords."""
    x = (float(xy_norm[0]) - meta.tx) / meta.scale
    y = (float(xy_norm[1]) - meta.ty) / meta.scale
    p_crop = np.linalg.inv(meta.M2) @ (np.array([x, y], dtype=np.float64) - meta.t)
    return float(p_crop[0]), float(p_crop[1])


def crop_to_source(xy_crop: tuple[float, float], meta: TransformMeta) -> tuple[float, float]:
    """Invert the crop step: crop coords -> source-image coords."""
    return float(xy_crop[0] + meta.bbox[0]), float(xy_crop[1] + meta.bbox[1])


def norm_to_source(xy_norm: tuple[float, float], meta: TransformMeta) -> tuple[float, float]:
    """Full inverse: 1024 coords -> source-image coords."""
    return crop_to_source(norm_to_crop(xy_norm, meta), meta)


def self_test_inversion(meta: TransformMeta, cal: dict) -> float:
    """Round-trip the bullseye (cal.cx, cal.cy in crop frame) through the forward
    transforms and back; return the recovery error in crop-px.

    Forward: crop -> warp -> 1024.  Inverse: 1024 -> crop.  Error should be < 1 px
    if all transforms are consistent.  Used by run_pipeline to fail-fast on bugs.
    """
    cx_crop, cy_crop = float(cal["cx"]), float(cal["cy"])
    p_warped = meta.M2 @ np.array([cx_crop, cy_crop], dtype=np.float64) + meta.t
    p_1024 = p_warped * meta.scale + np.array([meta.tx, meta.ty])
    p_crop_back = np.linalg.inv(meta.M2) @ ((p_1024 - np.array([meta.tx, meta.ty])) / meta.scale - meta.t)
    return float(np.hypot(p_crop_back[0] - cx_crop, p_crop_back[1] - cy_crop))
