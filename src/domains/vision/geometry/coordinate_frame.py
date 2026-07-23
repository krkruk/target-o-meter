"""The exact-analytical inverse chain — every transform that maps a 1024-frame
point back to its source-image pixel.

Ported verbatim from ``cv/approaches/iteredge/normalize.py`` (the
``IterEdgeTransformMeta`` dataclass + the inverse quadruple) at commit 76f6fc4.
Unifies the inverse chain behind one ``CoordinateFrame`` so the pipeline can
hand it through cleanly.

Inverse chain (1024 → source), exact analytical inverse:
  1. un-resize: ``p_warped = (p_1024 - (tx, ty)) / scale``
  2. un-warp:   ``p_crop ~ H_full^-1 @ p_warped``
  3. un-crop:   ``p_src = p_crop + (x0, y0)``

Because the inverse is exact and analytical, the bullseye round-trip error
should be ``< 1e-6 px`` (just float64 arithmetic).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _apply_H_inverse(pts: np.ndarray, H_inv: np.ndarray) -> np.ndarray:
    """Apply H_inv to Nx2 points (dehomogenize).
    cv/approaches/iteredge/normalize.py:99-108."""
    pts = np.asarray(pts, dtype=np.float64)
    if pts.size == 0:
        return pts.reshape(0, 2)
    homog = np.concatenate([pts, np.ones((pts.shape[0], 1))], axis=1)
    out = homog @ H_inv.T
    w = out[:, 2:3]
    w = np.where(np.abs(w) < 1e-12, 1e-12, w)
    return out[:, :2] / w


@dataclass
class CoordinateFrame:
    """All parameters needed to invert the forward transform chain.

    Unifies the cv/ ``IterEdgeTransformMeta`` dataclass + the four inverse
    helpers (``norm_to_crop`` / ``crop_to_source_xy`` / ``norm_to_warped`` /
    ``warped_to_crop``) into one type so callers pass the frame rather than
    a meta + module-function pair.
    """

    bbox: tuple[int, int, int, int]
    H_full: np.ndarray            # full homography: crop → warped
    H_full_inv: np.ndarray        # inverse: warped → crop
    out_size_warped: tuple[int, int]
    bullseye_warped: tuple[float, float]
    scale: float
    tx: float
    ty: float
    size: int                     # 1024
    r_ring1_warped: float
    cx_crop: float
    cy_crop: float

    # ---- inverse helpers (ported verbatim from normalize.py:111-131) ----

    def norm_to_warped(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.tx) / self.scale, (y - self.ty) / self.scale)

    def warped_to_crop(self, x: float, y: float) -> tuple[float, float]:
        p = _apply_H_inverse(
            np.array([[x, y]], dtype=np.float64), self.H_full_inv,
        )[0]
        return float(p[0]), float(p[1])

    def crop_to_source_xy(self, x: float, y: float) -> tuple[float, float]:
        return float(x + self.bbox[0]), float(y + self.bbox[1])

    def norm_to_crop(self, x: float, y: float) -> tuple[float, float]:
        wx, wy = self.norm_to_warped(x, y)
        return self.warped_to_crop(wx, wy)

    def norm_to_source(self, x: float, y: float) -> tuple[float, float]:
        cx, cy = self.norm_to_crop(x, y)
        return self.crop_to_source_xy(cx, cy)

    def self_test_inversion(self) -> float:
        """Round-trip the crop-frame bullseye ``(cx_crop, cy_crop)`` → 1024 →
        crop. Returns the recovery error in crop px (should be ``< 1e-6``).

        Ported verbatim from cv/approaches/iteredge/normalize.py:134-147.
        """
        cx_c, cy_c = self.cx_crop, self.cy_crop
        homog = np.array([cx_c, cy_c, 1.0], dtype=np.float64)
        p_w = self.H_full @ homog
        p_w = p_w[:2] / p_w[2]
        p_1024 = p_w * self.scale + np.array([self.tx, self.ty])
        p_back_warped = (p_1024 - np.array([self.tx, self.ty])) / self.scale
        p_back_crop = _apply_H_inverse(p_back_warped.reshape(1, 2), self.H_full_inv)[0]
        return float(np.hypot(p_back_crop[0] - cx_c, p_back_crop[1] - cy_c))
