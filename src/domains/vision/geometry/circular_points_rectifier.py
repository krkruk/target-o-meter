"""Stage 4 initial H — affine rectifier via the image of the circular points.

Ported verbatim from ``cv/approaches/multiring/homography.py`` (305 LOC at
commit 76f6fc4). ``projective_refine=False`` is mandatory (research §
architecture decision) — the affine rectifier is provably optimal for a flat
paper target.

Math is lifted as-is into class methods and module private helpers; only the
structure (free functions → ``CircularPointsRectifier`` class + helpers) changes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class RectificationResult:
    """Shape returned by ``CircularPointsRectifier.compute`` — preserved as the
    cv/ dict so downstream code reads keys unchanged."""

    H: np.ndarray
    H_inv: np.ndarray
    Q: np.ndarray
    center: np.ndarray
    circular_points: tuple[complex, complex]
    center_drift: np.ndarray | None
    used_projective: bool


def _ellipse_to_conic(
    cx: float, cy: float, semi_a: float, semi_b: float, angle_deg: float,
) -> np.ndarray:
    """3x3 symmetric conic matrix C such that x^T C x = 0 on the ellipse.

    Ported verbatim from cv/approaches/multiring/homography.py:68-97.
    """
    a = max(float(semi_a), 1e-6)
    b = max(float(semi_b), 1e-6)
    th = math.radians(angle_deg)
    cos_t, sin_t = math.cos(th), math.sin(th)
    R = np.array([
        [cos_t,  sin_t, 0],
        [-sin_t, cos_t, 0],
        [0,      0,     1],
    ], dtype=np.float64)
    T = np.array([
        [1, 0, -cx],
        [0, 1, -cy],
        [0, 0,  1 ],
    ], dtype=np.float64)
    D = np.diag([1.0 / (a * a), 1.0 / (b * b), -1.0])
    RT = R @ T
    return RT.T @ D @ RT


def _conic_2x2_block(C: np.ndarray) -> np.ndarray:
    """Upper-left 2x2 of a conic matrix (the image-plane metric Q).
    cv/approaches/multiring/homography.py:100-102."""
    return C[:2, :2]


def _average_shared_metric(
    rings: list[dict],
) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """Compute the average (A, B, C) metric across all rings, weighted by
    inverse semi-axis. cv/approaches/multiring/homography.py:108-146."""
    Qs: list[np.ndarray] = []
    ws: list[float] = []
    centers: list[np.ndarray] = []
    for r in rings:
        C = _ellipse_to_conic(r["cx"], r["cy"], r["semi_a"], r["semi_b"], r["angle_deg"])
        Q = _conic_2x2_block(C)
        ev = np.linalg.eigvalsh(Q)
        if ev.min() < 1e-9:
            continue
        Q_norm = Q / ev.min()
        w = 1.0 / max(math.sqrt(r["semi_a"] * r["semi_b"]), 1.0)
        Qs.append(Q_norm)
        ws.append(w)
        centers.append(np.array([r["cx"], r["cy"]], dtype=np.float64))
    if not Qs:
        raise ValueError("no usable rings for metric averaging")
    W = np.array(ws, dtype=np.float64)
    W /= W.sum()
    Q_avg = np.zeros((2, 2), dtype=np.float64)
    for Q, w in zip(Qs, W):
        Q_avg += w * Q
    Q_avg = 0.5 * (Q_avg + Q_avg.T)
    center = np.average(np.array(centers, dtype=np.float64), axis=0, weights=W)
    return Q_avg, center, [float(w) for w in ws]


def _matrix_inverse_sqrt(Q: np.ndarray) -> np.ndarray:
    """Principal inverse square root of a 2x2 SPD Q.
    cv/approaches/multiring/homography.py:149-158."""
    Q = 0.5 * (Q + Q.T)
    eigvals, eigvecs = np.linalg.eigh(Q)
    eigvals = np.clip(eigvals, 1e-9, None)
    return (eigvecs * (1.0 / np.sqrt(eigvals))) @ eigvecs.T


def _circular_points_from_Q(Q: np.ndarray) -> tuple[complex, complex]:
    """Solve A x² + 2 B xy + C y² = 0 for the two circular-point slopes.
    cv/approaches/multiring/homography.py:161-175."""
    A, B, C = Q[0, 0], Q[0, 1], Q[1, 1]
    disc = B * B - A * C
    if disc >= 0:
        return complex(0.0, 1.0), complex(0.0, -1.0)
    sq = math.sqrt(-disc)
    m1 = complex(-B / A, sq / A)
    m2 = complex(-B / A, -sq / A)
    return m1, m2


def _estimate_center_drift_projective(
    rings: list[dict], center: np.ndarray,
) -> np.ndarray | None:
    """Estimate a small projective ``center drift`` vector (vx, vy).
    cv/approaches/multiring/homography.py:181-223."""
    if len(rings) < 3:
        return None
    rs: list[float] = []
    dxs: list[float] = []
    dys: list[float] = []
    for r in rings:
        g = math.sqrt(r["semi_a"] * r["semi_b"])
        rs.append(g * g)
        dxs.append(r["cx"] - center[0])
        dys.append(r["cy"] - center[1])
    if max(rs) < 1e-6:
        return None
    A = np.array(rs, dtype=np.float64).reshape(-1, 1)
    vx, *_ = np.linalg.lstsq(A, np.array(dxs), rcond=None)
    vy, *_ = np.linalg.lstsq(A, np.array(dys), rcond=None)
    v = np.array([vx[0], vy[0]], dtype=np.float64)
    max_r = max(math.sqrt(r["semi_a"] * r["semi_b"]) for r in rings)
    expected_drift_at_max = np.linalg.norm(v) * max_r * max_r
    if expected_drift_at_max > 0.30 * max_r:
        return None
    return v


class CircularPointsRectifier:
    """Compute the 3x3 rectifying homography H that maps detected concentric
    ellipses to concentric circles centered at the origin.

    Ported verbatim from cv/approaches/multiring/homography.py:229-278.
    """

    @staticmethod
    def compute(
        rings: list[dict], projective_refine: bool = False,
    ) -> RectificationResult:
        if len(rings) < 2:
            raise ValueError(f"need ≥2 concentric rings for calibration, got {len(rings)}")

        Q, center, _ = _average_shared_metric(rings)
        Qinv_sqrt = _matrix_inverse_sqrt(Q)
        circular = _circular_points_from_Q(Q)

        H = np.eye(3, dtype=np.float64)
        H[:2, :2] = Qinv_sqrt
        H[:2, 2] = -Qinv_sqrt @ center

        used_projective = False
        center_drift = None
        if projective_refine:
            v = _estimate_center_drift_projective(rings, center)
            if v is not None:
                center_drift = v
                H[2, 0] = -2.0 * v[0]
                H[2, 1] = -2.0 * v[1]
                used_projective = True

        H_inv = np.linalg.inv(H)
        return RectificationResult(
            H=H,
            H_inv=H_inv,
            Q=Q,
            center=center,
            circular_points=circular,
            center_drift=center_drift,
            used_projective=used_projective,
        )

    @staticmethod
    def ring_radii_in_warped(rings: list[dict], H: np.ndarray) -> list[float]:
        """Map each detected ring's geometric-mean radius to its warped-frame
        radius. cv/approaches/multiring/homography.py:281-304.

        Used by the pipeline to compute the canonical 1-ring outer radius in
        warped px.
        """
        out: list[float] = []
        for r in rings:
            gmean = math.sqrt(r["semi_a"] * r["semi_b"])
            out.append(float(gmean))
        return out
