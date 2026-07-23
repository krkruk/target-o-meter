"""Canny/Sobel edges + ring-tangency weighting → the distance-transform
potential the refiner snaps to.

Ported verbatim from ``cv/approaches/iteredge/edges.py`` (127 LOC at commit
76f6fc4). Two potentials are produced:

  - ``dt_ring`` — distance transform of ring-weighted Canny edges (primary).
  - ``mag_smooth`` — smoothed ring-weighted magnitude (broad-basin signal).

The default ``potential`` returned is the all-Canny DT (not the ring-weighted
one) — cv/ observed ring_weight is computed at the initial center and becomes
inaccurate as the warp shifts.

Math is lifted as-is into class methods; only structure changes.
"""
from __future__ import annotations

import cv2
import numpy as np


class EdgePotential:
    """Edge maps + ring-tangency weighting. Ported from iteredge/edges.py."""

    @staticmethod
    def sobel_magnitude(gray: np.ndarray, blur_sigma: float = 1.5) -> np.ndarray:
        """cv/approaches/iteredge/edges.py:30-35."""
        ksize = max(3, int(2 * round(blur_sigma) + 1))
        blur = cv2.GaussianBlur(gray.astype(np.float32), (ksize, ksize), blur_sigma)
        gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
        return np.sqrt(gx * gx + gy * gy)

    @staticmethod
    def canny_edges(
        gray: np.ndarray, low: float = 40.0, high: float = 120.0, blur_sigma: float = 1.5,
    ) -> np.ndarray:
        """cv/approaches/iteredge/edges.py:38-41."""
        ksize = max(3, int(2 * round(blur_sigma) + 1))
        blur = cv2.GaussianBlur(gray, (ksize, ksize), blur_sigma)
        return cv2.Canny(blur, low, high, apertureSize=3, L2gradient=True)

    @staticmethod
    def edge_distance_transform(canny_bin: np.ndarray) -> np.ndarray:
        """Distance to the nearest Canny edge (float32, L2).
        cv/approaches/iteredge/edges.py:44-48."""
        inv = (canny_bin == 0).astype(np.uint8)
        dt = cv2.distanceTransform(inv, cv2.DIST_L2, 5)
        return dt.astype(np.float32)

    @staticmethod
    def enhance_ring_edges(
        gray: np.ndarray, cx: float, cy: float, s_px: float,
        smooth_sigma: float | None = None,
    ) -> dict:
        """Build the edge maps used by the optimizer.

        Returns dict with keys ``sobel``, ``canny``, ``dt``, ``dt_ring``,
        ``ring_weight``, ``mag_smooth``, ``potential``. Ported verbatim from
        cv/approaches/iteredge/edges.py:51-127.
        """
        h, w = gray.shape
        if smooth_sigma is None:
            smooth_sigma = max(1.5, s_px / 6.0)

        gx = cv2.Sobel(gray.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
        mag = np.sqrt(gx * gx + gy * gy)

        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        dx = xx - cx
        dy = yy - cy
        r = np.sqrt(dx * dx + dy * dy) + 1e-6
        rx = dx / r
        ry = dy / r

        gmag = np.maximum(mag, 1e-6)
        gdir_x = gx / gmag
        gdir_y = gy / gmag

        dot = np.abs(gdir_x * rx + gdir_y * ry)
        ring_weight = 1.0 - dot
        falloff = np.clip(r / (3.0 * max(s_px, 1.0)), 0.0, 1.0) * np.clip(
            1.0 - (r / (min(w, h) * 0.55)), 0.0, 1.0
        )
        ring_weight = ring_weight * falloff

        canny = EdgePotential.canny_edges(gray)
        dt = EdgePotential.edge_distance_transform(canny)

        ring_canny = ((canny > 0) & (ring_weight > 0.4)).astype(np.uint8) * 255
        if ring_canny.any():
            dt_ring = EdgePotential.edge_distance_transform(ring_canny)
        else:
            dt_ring = dt.copy()

        weighted_mag = mag * ring_weight
        ksize = max(3, int(2 * round(smooth_sigma) + 1) | 1)
        mag_smooth = cv2.GaussianBlur(weighted_mag, (ksize, ksize), smooth_sigma)

        return {
            "sobel": mag,
            "canny": canny,
            "dt": dt,
            "dt_ring": dt_ring,
            "ring_weight": ring_weight.astype(np.float32),
            "mag_smooth": mag_smooth.astype(np.float32),
            "potential": dt,
        }
