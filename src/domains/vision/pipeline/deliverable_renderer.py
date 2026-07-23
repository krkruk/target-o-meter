"""Magenta-dot deliverable renderer.

Ported verbatim from ``cv/phase3_spike/viz.py`` (110 LOC at commit 76f6fc4).
Draws LLM holes as magenta dots (radius ∝ caliber, 70% of hole diameter per
the Step-2 spec) + faint canonical ring frame + score labels.

The diameter table is owned by ``CaliberTaxonomy`` (single source of truth);
this module reads it through ``CaliberTaxonomy.diameter_mm``.
"""
from __future__ import annotations

import cv2
import numpy as np

from src.domains.vision.pipeline.caliber_taxonomy import CaliberTaxonomy
from src.domains.vision.ports import TargetType


# ISSF ring-1 radius (mm) per target type. Ring 1 = outermost scoring ring.
# Ported from cv/phase3_spike/viz.py:24-27.
_RING1_RADIUS_MM = {
    "air_pistol": 5.75 + 8.0 * 9,       # 77.75 mm
    "precision_pistol": 25.0 + 25.0 * 9,  # 250.0 mm
}

# Marker diameter as a fraction of the hole's caliber diameter (per the spec).
_MARKER_DIAMETER_FRACTION = 0.70


class DeliverableRenderer:
    """Draw magenta holes + canonical ring frame + score labels."""

    @staticmethod
    def px_per_mm(target_type: TargetType, target_ring1_px: float) -> float:
        """Convert ring-1 radius in 1024-frame px to px-per-mm.
        ``target_ring1_px / ring1_radius_mm = px_per_mm``.

        Ported from cv/phase3_spike/viz.py:44-50.
        """
        ring1_mm = _RING1_RADIUS_MM[target_type]
        return float(target_ring1_px) / ring1_mm

    @staticmethod
    def marker_radius_px(
        caliber_raw: str,
        target_type: TargetType,
        target_ring1_px: float,
        fraction: float = _MARKER_DIAMETER_FRACTION,
    ) -> int:
        """Radius in px of a magenta dot for a hole of the given caliber.

        ``marker_diameter = fraction * hole_diameter = fraction * caliber_diameter_mm * px_per_mm``
        ``marker_radius   = marker_diameter / 2``

        Ported from cv/phase3_spike/viz.py:53-68.
        """
        diam_mm = CaliberTaxonomy.diameter_mm(caliber_raw)
        ppmm = DeliverableRenderer.px_per_mm(target_type, target_ring1_px)
        radius = (fraction * diam_mm * ppmm) / 2.0
        return max(2, int(round(radius)))

    @staticmethod
    def draw_magenta_holes(
        image_1024_gray: np.ndarray,
        holes: list[dict],
        target_type: TargetType,
        target_ring1_px: float,
        with_score: bool = True,
    ) -> np.ndarray:
        """Draw magenta dots (proportional to caliber) + score labels on a 1024 image.

        Ported verbatim from cv/phase3_spike/viz.py:71-110.

        Args:
            image_1024_gray: the grayscale 1024x1024 LLM-input image (uint8).
            holes: list of dicts with keys ``x``, ``y``, ``score``, ``confidence``,
                   ``caliber`` (as produced by ``Hole.model_dump()`` /
                   ``DetectedHole.to_dict()``).
            target_type / target_ring1_px: geometry for caliber→px sizing.
            with_score: draw the ISSF score next to each dot.

        Returns:
            BGR image (for ``cv2.imwrite``).
        """
        bgr = cv2.cvtColor(image_1024_gray, cv2.COLOR_GRAY2BGR)
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
            r = DeliverableRenderer.marker_radius_px(
                h.get("caliber", ""), target_type, target_ring1_px,
            )
            cv2.circle(bgr, (x, y), r, (255, 0, 255), -1)
            cv2.circle(bgr, (x, y), r, (255, 255, 255), 1)
            if with_score:
                cv2.putText(
                    bgr, str(int(h.get("score", 0))),
                    (x + r + 2, y + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 255), 2,
                )
        return bgr
