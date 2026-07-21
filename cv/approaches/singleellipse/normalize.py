"""Normalize the warped image to 1024×1024 + provide inverse transform chain.

After cv.approaches.singleellipse.warp.apply_warp, the warped image is a
square with the (now-circular) disc at its center (out_size/2, out_size/2).
This module:

  1. Derives s_px in warped px directly from the warped disc radius using ISSF
     air-pistol geometry (r_bw = disc radius; r_bull = r_bw / 7.391;
     s_px = (r_bw - r_bull) / 3). The existing cv.blob_detect.calibrate was
     unreliable here because it re-detects the disc on a huge warped image
     and frequently picks up the merged disc + dark-rings blob, yielding
     wildly wrong r_bw.
  2. Resizes + translates to 1024×1024 with bullseye at (512, 512) and the
     1-ring boundary at radius 500 px.
  3. Provides the inverse chain (1024 → crop → source).

The inverse chain has three stages, each with its own inverse:
  - to_llm_square:    scale + translate.                     inverse: (p - t)/scale.
  - apply_warp:       3×3 homography (H_total).              inverse: H_total⁻¹.
  - crop:             integer translation by (x0, y0).       inverse: p + (x0, y0).

The full inverse maps a 1024-coord point back to source-image pixels.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


# ISSF air-pistol ring geometry (mm):
#   r_bull (10-ring outer) = 11.5 mm
#   r_bw (black/white boundary, ring-7 outer) = 85.0 mm
#   r_1 (ring-1 outer) = 232.0 mm
# Ratio r_bw / r_bull = 7.391 (used to derive s_px from r_bw alone).
ISSF_R_BULL_MM = 11.5
ISSF_R_BW_MM = 85.0
ISSF_R_BULL_OVER_BW = ISSF_R_BULL_MM / ISSF_R_BW_MM  # ≈ 0.1353


@dataclass
class WarpMeta:
    """Geometry of the warp stage, for inversion."""
    H_total: np.ndarray        # 3×3 homography crop → warped
    H_total_inv: np.ndarray    # inverse homography warped → crop
    out_size: int              # warped image is (out_size, out_size)


@dataclass
class NormMeta:
    """Full transform chain from source-crop to 1024-coords."""
    bbox: tuple[int, int, int, int]
    H_total: np.ndarray
    H_total_inv: np.ndarray
    out_size: int
    scale: float               # resize factor warped → 1024-frame
    tx: float                  # translation x (1024-frame)
    ty: float                  # translation y (1024-frame)
    size: int                  # 1024
    # Calibration in warped px:
    disc_center_warped: tuple[float, float]
    r_bw_warped: float
    r_bull_warped: float
    s_px_warped: float
    calibrate_ok: bool


def calibrate_warped(
    warped: np.ndarray,
    disc_center_warped: tuple[float, float],
    disc_radius_warped: float,
) -> dict:
    """Derive (r_bw, r_bull, s_px) in warped px from the warped disc radius.

    Uses ISSF air-pistol geometry: the black scoring disc's outer edge is the
    black/white boundary (between rings 6 and 7), which is r_bw = r_bull +
    3·s. With r_bull/r_bw = 11.5/85 = 0.1353, we recover both r_bull and s
    from the measured disc radius alone.

    Returns dict with r_bw_warped, r_bull_warped, s_px_warped, calibrate_ok.
    """
    r_bw = float(disc_radius_warped)
    r_bull = r_bw * ISSF_R_BULL_OVER_BW
    s_px = (r_bw - r_bull) / 3.0
    return {
        "r_bw_warped": r_bw, "r_bull_warped": r_bull,
        "s_px_warped": s_px, "calibrate_ok": True,
    }


def to_llm_square(
    warped: np.ndarray,
    warp_meta: WarpMeta,
    disc_center_warped: tuple[float, float],
    disc_radius_warped: float,
    bbox: tuple[int, int, int, int],
    target_ring1_px: float = 500.0,
    size: int = 1024,
    fill_value: int = 245,
) -> tuple[np.ndarray, NormMeta]:
    """Resize+translate the warped image to a 1024×1024 square.

    Layout: bullseye at (size/2, size/2); 1-ring outer boundary at radius
    target_ring1_px. Outside the visible target area: fill_value.

    The bullseye location in the warped frame is `disc_center_warped` shifted
    inward by `r_bull_warped` along the line from the calibrated bullseye
    toward disc center. For a fronto-parallel disc, the bullseye IS the disc
    center (no off-centre bull). We assume that here.
    """
    cal = calibrate_warped(warped, disc_center_warped, disc_radius_warped)
    r_bw_warped = cal["r_bw_warped"]
    r_bull_warped = cal["r_bull_warped"]
    s_px_warped = cal["s_px_warped"]
    cal_ok = cal["calibrate_ok"]

    # r_ring1 in warped px = r_bull + 9*s.
    r_ring1_warped = r_bull_warped + 9.0 * s_px_warped
    if r_ring1_warped <= 0:
        r_ring1_warped = float(max(warped.shape)) / 2.0
    scale = float(target_ring1_px) / r_ring1_warped

    new_w = max(1, int(round(warped.shape[1] * scale)))
    new_h = max(1, int(round(warped.shape[0] * scale)))
    resized = cv2.resize(warped, (new_w, new_h), interpolation=cv2.INTER_AREA)

    # Disc center in resized frame = (bullseye_warped * scale) approximately.
    # We treat the disc center as the bullseye for the fronto-parallel case.
    bsx_r = disc_center_warped[0] * scale
    bsy_r = disc_center_warped[1] * scale

    target_cx = size / 2.0
    target_cy = size / 2.0
    tx = target_cx - bsx_r
    ty = target_cy - bsy_r

    canvas = np.full((size, size), fill_value, dtype=np.uint8)
    dst_x0 = int(round(tx)); dst_y0 = int(round(ty))
    src_x0 = max(0, -dst_x0)
    src_y0 = max(0, -dst_y0)
    src_x1 = min(new_w, size - dst_x0)
    src_y1 = min(new_h, size - dst_y0)
    out_x0 = max(0, dst_x0)
    out_y0 = max(0, dst_y0)
    out_x1 = min(size, dst_x0 + new_w)
    out_y1 = min(size, dst_y0 + new_h)
    if (out_x1 > out_x0 and out_y1 > out_y0
            and src_x1 > src_x0 and src_y1 > src_y0):
        canvas[out_y0:out_y1, out_x0:out_x1] = resized[src_y0:src_y1, src_x0:src_x1]

    meta = NormMeta(
        bbox=tuple(int(v) for v in bbox),
        H_total=warp_meta.H_total,
        H_total_inv=warp_meta.H_total_inv,
        out_size=int(warp_meta.out_size),
        scale=float(scale),
        tx=float(tx),
        ty=float(ty),
        size=int(size),
        disc_center_warped=tuple(float(v) for v in disc_center_warped),
        r_bw_warped=float(r_bw_warped),
        r_bull_warped=float(r_bull_warped),
        s_px_warped=float(s_px_warped),
        calibrate_ok=bool(cal_ok),
    )
    return canvas, meta


# ---------------------------------------------------------------------------
# Inverse transform chain
# ---------------------------------------------------------------------------
def norm_to_warped(xy_norm: tuple[float, float], meta: NormMeta) -> tuple[float, float]:
    """1024 coords → warped-frame px (inverts the resize + translate)."""
    x = (float(xy_norm[0]) - meta.tx) / meta.scale
    y = (float(xy_norm[1]) - meta.ty) / meta.scale
    return x, y


def warped_to_crop(xy_warped: tuple[float, float], meta: NormMeta) -> tuple[float, float]:
    """Warped-frame px → crop-frame px (inverts the homography)."""
    p = meta.H_total_inv @ np.array([xy_warped[0], xy_warped[1], 1.0])
    return float(p[0] / p[2]), float(p[1] / p[2])


def crop_to_source(xy_crop: tuple[float, float], meta: NormMeta) -> tuple[float, float]:
    """Crop-frame px → source-image px (inverts the crop offset)."""
    return float(xy_crop[0] + meta.bbox[0]), float(xy_crop[1] + meta.bbox[1])


def norm_to_source(xy_norm: tuple[float, float], meta: NormMeta) -> tuple[float, float]:
    """Full inverse: 1024 coords → source-image px."""
    return crop_to_source(warped_to_crop(norm_to_warped(xy_norm, meta), meta), meta)


def self_test_inversion(
    meta: NormMeta,
    crop_disc_center: tuple[float, float],
) -> float:
    """Round-trip the disc center through the forward chain and back.

    Forward (manual): crop_disc_center → warped → 1024.
    Inverse (via norm_to_source etc.): 1024 → crop.
    Returns the recovery error in pixels (in crop frame).
    """
    cx, cy = crop_disc_center
    # Forward to warped
    p = meta.H_total @ np.array([cx, cy, 1.0])
    p = p / p[2]
    wx, wy = float(p[0]), float(p[1])
    # Forward to 1024
    nx = wx * meta.scale + meta.tx
    ny = wy * meta.scale + meta.ty
    # Inverse back to crop
    rx, ry = warped_to_crop(norm_to_warped((nx, ny), meta), meta)
    return float(math.hypot(rx - cx, ry - cy))
