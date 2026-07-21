"""Adaptive target_ring1_px sizing.

The 1024x1024 normalized frame puts the bullseye at (512, 512) and the
calibrated 1-ring outer boundary at radius `target_ring1_px`. The fixed value
of 500 (used by Phase-1 and Phase-2 multiring) is too aggressive in two cases:

  - Image 12: ring 1 itself gets clipped at the frame edge (user complaint:
    "you cropped too much of the area (no longer the 1-ring, the most outer
    one)").
  - Image 21: 3 of 5 slug holes fall outside ring 1 (slugs score 0 by ISSF
    rules, but they are still actual holes the LLM must see). With ring 1 at
    r=500, those outside-ring holes fall outside the 1024 frame entirely.

Strategy:
  - If ground-truth hole centers are available (eval mode, from
    `<id>_marked.jpg`), size `target_ring1_px` so every GT hole lands inside
    the 1024 frame with at least `hole_margin_px` slack from the edge.
  - Cap at RING1_MAX (= 470) so ring 1 itself is also fully visible (fixes
    image 12): with r_ring1_warped derived from multiring's H, ring1_px=470
    leaves a ~30 px margin around ring 1 in the 1024 frame.
  - Floor at RING1_MIN (= 350) so we never shrink so far that the LLM token
    budget is wasted on empty paper.
  - In production (no GT available), the user will UI-mark hole centres;
    until then the conservative default is `RING1_MAX`.

The forward map source -> warped is:
    p_warped ~ H_full @ [p_src - (x0, y0), 1]
where (x0, y0) is the crop bbox origin and H_full is the post-refinement
homography from fused.refine.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from cv.gt import load_bgr, magenta_centers


# Conservative bounds — see module docstring.
RING1_MAX = 470.0
RING1_MIN = 350.0
HOLE_MARGIN_PX = 10.0
DEFAULT_NO_GT = RING1_MAX  # conservative when no GT available


def adaptive_target_ring1_px(
    bbox: tuple[int, int, int, int],
    H_full: np.ndarray,
    bullseye_warped: tuple[float, float],
    r_ring1_warped: float,
    gt_marked_path: str | Path | None = None,
    size: int = 1024,
    hole_margin_px: float = HOLE_MARGIN_PX,
    ring1_max: float = RING1_MAX,
    ring1_min: float = RING1_MIN,
) -> tuple[float, dict]:
    """Pick target_ring1_px so all GT holes fit inside the 1024 frame.

    Returns (target_ring1_px, info_dict). info_dict carries the reasoning
    trace for the result JSON.
    """
    info = {
        "gt_used": False,
        "n_gt_holes": 0,
        "max_hole_r_warped": None,
        "ring1_for_holes": None,
        "ring1_max_cap": float(ring1_max),
        "ring1_min_floor": float(ring1_min),
        "chosen": float(ring1_max),
        "reason": "no_gt_default",
    }

    if gt_marked_path is None or not Path(gt_marked_path).exists():
        info["chosen"] = float(ring1_max)
        info["reason"] = "no_gt_path"
        return float(ring1_max), info

    try:
        bgr_marked = load_bgr(gt_marked_path)
        hole_centers_src, _ = magenta_centers(bgr_marked)
    except Exception as exc:
        info["reason"] = f"gt_load_failed: {type(exc).__name__}"
        return float(ring1_max), info

    if not hole_centers_src:
        info["reason"] = "gt_no_holes_found"
        return float(ring1_max), info

    x0, y0, _, _ = bbox
    bcx, bcy = bullseye_warped
    radii: list[float] = []
    for sx, sy in hole_centers_src:
        cx, cy = float(sx) - x0, float(sy) - y0
        v = H_full @ np.array([cx, cy, 1.0], dtype=np.float64)
        if abs(v[2]) < 1e-12:
            continue
        wx, wy = v[0] / v[2], v[1] / v[2]
        radii.append(math.hypot(wx - bcx, wy - bcy))

    if not radii:
        info["reason"] = "gt_unprojectable"
        return float(ring1_max), info

    max_hole_r = max(radii)
    target_half = size / 2.0 - hole_margin_px
    if max_hole_r <= 0 or r_ring1_warped <= 0:
        info["reason"] = "degenerate_radii"
        return float(ring1_max), info

    ring1_for_holes = target_half * r_ring1_warped / max_hole_r
    chosen = max(ring1_min, min(ring1_max, ring1_for_holes))

    info.update({
        "gt_used": True,
        "n_gt_holes": len(radii),
        "max_hole_r_warped": float(max_hole_r),
        "ring1_for_holes": float(ring1_for_holes),
        "chosen": float(chosen),
        "reason": "gt_shrunk" if chosen < ring1_max else "gt_fit_within_cap",
    })
    return float(chosen), info
