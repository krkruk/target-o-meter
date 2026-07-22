"""Magenta-dot visualization for Phase 3 spike results.

Draws the LLM's detected holes as magenta dots on the 1024x1024 normalized
image, with each dot's radius proportional to the hole's caliber (70% of the
hole's diameter, per the Step-2 spec). The px-per-mm scale is derived from the
fused pipeline's per-image ring-1 geometry.

Caliber diameters are nominal bullet diameters (mm). A bullet hole in paper is
typically close to the bullet diameter; 70% of that diameter is the marker
size — small enough to verify the LLM's centroid, large enough to see.
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from cv.detector_base import TargetType
from cv.phase3_spike.metadata import normalize_caliber

# ISSF ring-1 radius (mm) per target type — from the dormant ISSF_RADII_MM table
# (cv/tmp/probe_ring_calibration_v6.py). Ring 1 = outermost scoring ring.
_RING1_RADIUS_MM = {
    "air_pistol": 5.75 + 8.0 * 9,       # 77.75 mm
    "precision_pistol": 25.0 + 25.0 * 9,  # 250.0 mm
}

# Nominal bullet diameters (mm). Used to size magenta markers.
# .22lr and .223Rem are nearly identical (5.7 vs 5.56) — both map near 5.6 mm.
_CALIBER_DIAMETER_MM: dict[str, float] = {
    "22lr": 5.7,
    ".223rem": 5.56,
    "9mm": 9.01,
    ".45acp": 11.5,
    "7.62x39": 7.9,
    "12-gauge": 18.0,  # slug; shot-cup spread varies, slug is ~18 mm
}

_DEFAULT_DIAMETER_MM = 9.0  # fallback when the LLM's caliber string is unrecognized
_MARKER_DIAMETER_FRACTION = 0.70  # "70% of the hole" per the spec


def px_per_mm(target_type: TargetType, target_ring1_px: float) -> float:
    """Convert ring-1 radius in 1024-frame px to px-per-mm.

    target_ring1_px / ring1_radius_mm = px_per_mm.
    """
    ring1_mm = _RING1_RADIUS_MM[target_type]
    return float(target_ring1_px) / ring1_mm


def marker_radius_px(
    caliber_raw: str,
    target_type: TargetType,
    target_ring1_px: float,
    fraction: float = _MARKER_DIAMETER_FRACTION,
) -> int:
    """Radius in px of a magenta dot for a hole of the given caliber.

    marker_diameter = fraction * hole_diameter = fraction * caliber_diameter_mm * px_per_mm
    marker_radius   = marker_diameter / 2
    """
    cal = normalize_caliber(caliber_raw).lower()
    diam_mm = _CALIBER_DIAMETER_MM.get(cal, _DEFAULT_DIAMETER_MM)
    ppmm = px_per_mm(target_type, target_ring1_px)
    radius = (fraction * diam_mm * ppmm) / 2.0
    return max(2, int(round(radius)))


def draw_magenta_holes(
    image_1024_gray: np.ndarray,
    holes: list[dict],
    target_type: TargetType,
    target_ring1_px: float,
    with_score: bool = True,
) -> np.ndarray:
    """Draw magenta dots (proportional to caliber) + score labels on a 1024 image.

    Args:
        image_1024_gray: the grayscale 1024x1024 LLM-input image (uint8).
        holes: list of dicts with keys x, y, score, confidence, caliber
               (as produced by Hole.model_dump()).
        target_type / target_ring1_px: geometry for caliber→px sizing.
        with_score: draw the ISSF score next to each dot.

    Returns:
        BGR image (for cv2.imwrite).
    """
    bgr = cv2.cvtColor(image_1024_gray, cv2.COLOR_GRAY2BGR)
    # Faint canonical ring frame so the user can sanity-check ring placement.
    bcx = bcy = 512
    ring_step = float(target_ring1_px) / 9.0
    for k in range(1, 11):
        r = int(round(k * ring_step))
        col = (0, 255, 255) if k == 10 else (0, 200, 0) if k == 7 else (60, 200, 60)
        thick = 2 if k in (1, 7, 10) else 1
        cv2.circle(bgr, (bcx, bcy), r, col, thick)
    cv2.circle(bgr, (bcx, bcy), 5, (0, 0, 255), -1)

    for h in holes:
        x, y = int(h["x"]), int(h["y"])
        r = marker_radius_px(h.get("caliber", ""), target_type, target_ring1_px)
        cv2.circle(bgr, (x, y), r, (255, 0, 255), -1)
        cv2.circle(bgr, (x, y), r, (255, 255, 255), 1)  # outline for visibility
        if with_score:
            cv2.putText(bgr, str(int(h.get("score", 0))),
                        (x + r + 2, y + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 255), 2)
    return bgr
