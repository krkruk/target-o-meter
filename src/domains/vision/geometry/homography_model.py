"""8-DOF homography parametrization + ring-point prediction used by the refiner.

Ported verbatim from ``cv/approaches/iteredge/model.py`` (128 LOC at commit
76f6fc4). Forward chain: ``[x_w, y_w, 1]^T ~ H @ [x_c, y_c, 1]^T`` (then
divide by w). Rings in the warped frame are concentric circles; sampling them
at uniform angles and mapping back through ``H^-1`` gives the source-frame
positions where the optimizer samples the edge potential.

Math is lifted as-is into class methods; only structure changes.
"""
from __future__ import annotations

import cv2
import numpy as np


# A homography is 3x3 with H[2,2] = 1 → 8 free params.
HOMOGRAPHY_DOFS = 8


class HomographyModel:
    """8-DOF homography parametrization. Ported from iteredge/model.py."""

    @staticmethod
    def params_to_H(params: np.ndarray) -> np.ndarray:
        """Convert an 8-vector to a 3x3 homography (float64).
        cv/approaches/iteredge/model.py:37-44."""
        p = np.asarray(params, dtype=np.float64)
        return np.array([
            [p[0], p[1], p[2]],
            [p[3], p[4], p[5]],
            [p[6], p[7], 1.0],
        ], dtype=np.float64)

    @staticmethod
    def H_to_params(H: np.ndarray) -> np.ndarray:
        """Convert a 3x3 homography to an 8-vector (normalises H[2,2]=1).
        cv/approaches/iteredge/model.py:47-55."""
        H = np.asarray(H, dtype=np.float64)
        H = H / H[2, 2]
        return np.array([
            H[0, 0], H[0, 1], H[0, 2],
            H[1, 0], H[1, 1], H[1, 2],
            H[2, 0], H[2, 1],
        ], dtype=np.float64)

    @staticmethod
    def affine_init_params(M2: np.ndarray, t_xy: np.ndarray) -> np.ndarray:
        """Build the 8-vector for an affine transform (perspective terms = 0).
        cv/approaches/iteredge/model.py:58-64."""
        return np.array([
            M2[0, 0], M2[0, 1], float(t_xy[0]),
            M2[1, 0], M2[1, 1], float(t_xy[1]),
            0.0, 0.0,
        ], dtype=np.float64)

    @staticmethod
    def apply_H_to_points(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
        """Apply homography to an Nx2 array; returns Nx2 (dehomogenized).
        cv/approaches/iteredge/model.py:67-76."""
        pts = np.asarray(pts, dtype=np.float64)
        if pts.size == 0:
            return pts.reshape(0, 2)
        homog = np.concatenate([pts, np.ones((pts.shape[0], 1))], axis=1)  # N×3
        out = homog @ H.T  # N×3
        w = out[:, 2:3]
        w = np.where(np.abs(w) < 1e-12, 1e-12, w)
        return out[:, :2] / w

    @staticmethod
    def ring_points_warped(
        ocx: float, ocy: float, r_bull_warped: float, s_warped: float,
        n_rings: int = 10, n_per_ring: int = 64,
        r_min_factor: float = 1.0, r_max_factor: float = 10.0,
    ) -> np.ndarray:
        """Sample points around n_rings concentric circles in the warped frame.
        cv/approaches/iteredge/model.py:82-106."""
        radii = [r_bull_warped + k * s_warped for k in range(n_rings)
                 if r_min_factor <= (1.0 + k) <= r_max_factor]
        angles = np.linspace(0.0, 2.0 * np.pi, n_per_ring, endpoint=False)
        pts = []
        for r in radii:
            if r <= 0:
                continue
            for a in angles:
                pts.append((ocx + r * np.cos(a), ocy + r * np.sin(a)))
        return np.array(pts, dtype=np.float64)

    @staticmethod
    def sample_potential(potential: np.ndarray, pts: np.ndarray) -> np.ndarray:
        """Bilinear-sample the potential at each (x, y) point; returns values.
        cv/approaches/iteredge/model.py:112-128."""
        pts = np.asarray(pts, dtype=np.float32)
        if pts.size == 0:
            return np.zeros(0, dtype=np.float32)
        xs = pts[:, 0].reshape(1, -1).astype(np.float32)
        ys = pts[:, 1].reshape(1, -1).astype(np.float32)
        sampled = cv2.remap(
            potential, xs, ys,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0.0,
        )
        return sampled.ravel()
