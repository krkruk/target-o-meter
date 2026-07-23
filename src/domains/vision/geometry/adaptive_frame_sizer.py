"""Stage 6 sizing — GT-hole-extent-aware ``margin_factor`` so the whole warp
canvas fits 1024 with no content crop.

Ported verbatim from ``cv/approaches/fused/adaptive_frame.py`` (125 LOC at
commit 76f6fc4).

Two failure modes the sizer fixes:
  1. Normalization crops warp content (img 12 regression) → fit the ENTIRE
     warp canvas into 1024, no content lost between warp and LLM input.
  2. Warp itself crops holes beyond ``margin_factor × r_ring1`` (img 21
     slugs at ~1.31× ring 1 vs default 1.30×) → enlarge ``margin_factor``
     when GT hole extent demands it.

Constants travel here per the one-class-per-file rule.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from src.domains.vision.eval.magenta_gt import load_bgr, magenta_centers


DEFAULT_MARGIN_FACTOR = 1.30
HOLE_MARGIN_FACTOR = 1.10     # 10% slack beyond the outermost GT hole
MAX_MARGIN_FACTOR = 2.50      # cap to prevent runaway canvas sizes


class AdaptiveFrameSizer:
    """Pick the warp ``margin_factor`` that ensures ring 1 AND all GT holes
    fit inside the warp canvas.

    Ported from cv/approaches/fused/adaptive_frame.py:46-124.
    """

    @staticmethod
    def margin_factor(
        bbox: tuple[int, int, int, int],
        H_opt: np.ndarray,
        cx_crop: float,
        cy_crop: float,
        r_ring1_warped: float,
        gt_marked_path: str | Path | None = None,
        default_margin_factor: float = DEFAULT_MARGIN_FACTOR,
        hole_margin_factor: float = HOLE_MARGIN_FACTOR,
        max_margin_factor: float = MAX_MARGIN_FACTOR,
    ) -> tuple[float, dict]:
        """Returns ``(margin_factor, info_dict)``. info_dict carries the
        reasoning trace for the result JSON."""
        info = {
            "gt_used": False,
            "n_gt_holes": 0,
            "max_hole_r_warped": None,
            "hole_to_ring1_ratio": None,
            "margin_factor_default": float(default_margin_factor),
            "margin_factor_for_holes": None,
            "margin_factor_chosen": float(default_margin_factor),
            "max_margin_factor_cap": float(max_margin_factor),
            "reason": "no_gt_default",
        }

        if gt_marked_path is None or not Path(gt_marked_path).exists():
            return float(default_margin_factor), info

        try:
            bgr_marked = load_bgr(str(gt_marked_path))
            hole_centers_src, _ = magenta_centers(bgr_marked)
        except Exception as exc:
            info["reason"] = f"gt_load_failed: {type(exc).__name__}"
            return float(default_margin_factor), info

        if not hole_centers_src:
            info["reason"] = "gt_no_holes_found"
            return float(default_margin_factor), info

        x0, y0, _, _ = bbox
        v_bull = H_opt @ np.array([cx_crop, cy_crop, 1.0], dtype=np.float64)
        if abs(v_bull[2]) < 1e-12:
            info["reason"] = "bullseye_unprojectable"
            return float(default_margin_factor), info
        bull_warped = v_bull[:2] / v_bull[2]

        radii: list[float] = []
        for sx, sy in hole_centers_src:
            cx, cy = float(sx) - x0, float(sy) - y0
            v = H_opt @ np.array([cx, cy, 1.0], dtype=np.float64)
            if abs(v[2]) < 1e-12:
                continue
            wx, wy = v[0] / v[2], v[1] / v[2]
            radii.append(math.hypot(wx - bull_warped[0], wy - bull_warped[1]))

        if not radii or r_ring1_warped <= 0:
            info["reason"] = "gt_unprojectable"
            return float(default_margin_factor), info

        max_hole_r = max(radii)
        hole_to_ring1 = max_hole_r / r_ring1_warped
        margin_for_holes = hole_to_ring1 * hole_margin_factor
        chosen = max(default_margin_factor, min(margin_for_holes, max_margin_factor))

        info.update({
            "gt_used": True,
            "n_gt_holes": len(radii),
            "max_hole_r_warped": float(max_hole_r),
            "hole_to_ring1_ratio": float(hole_to_ring1),
            "margin_factor_for_holes": float(margin_for_holes),
            "margin_factor_chosen": float(chosen),
            "reason": "gt_enlarged" if chosen > default_margin_factor else "default_sufficient",
        })
        return float(chosen), info
