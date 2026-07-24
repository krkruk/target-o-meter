"""ISSF line-break scoring — ``IssfScorer``.

Ported verbatim from ``cv/blob_detect.py:604-614``. Constants
``RING_STEPS_BW_TO_BULL`` and ``BULLET_RADIUS_MM`` travel with this class —
they are scoped to scoring only.
"""
from __future__ import annotations

import math

from src.domains.vision.geometry.calibration import Calibration


# Ring-index prior: the black/white boundary is between rings 6 and 7, i.e.
# the outer edge of ring 7 = 3 ring-steps outside the 10-ring (bullseye).
RING_STEPS_BW_TO_BULL = 3

# Caliber → bullet radius table (carried verbatim from cv/blob_detect.py:35).
BULLET_RADIUS_MM = {"22lr": 2.85, "9x19": 4.5, ".223Rem": 2.78, "slug": 9.0}


class IssfScorer:
    """ISSF line-break scoring — ``cv/blob_detect.py:604-614`` verbatim.

    ``score = 10 - ceil((dist(bull,hole) - r_hole - r_bull)/s)``, clamped to
    ``[0, 10]``. Uses the *detected* hole radius (user direction).
    """

    @staticmethod
    def score_holes(
        holes: list[tuple[float, float, float]],
        cal: Calibration,
    ) -> list[int]:
        s, r_bull = cal.s_px, cal.r_bull_px
        cx, cy = cal.cx, cal.cy
        scores: list[int] = []
        for x, y, r in holes:
            d = math.hypot(x - cx, y - cy) - r           # line-break: subtract hole radius
            steps = int(math.ceil((d - r_bull) / s)) if d > r_bull else 0
            scores.append(max(0, min(10, 10 - steps)))
        return scores
