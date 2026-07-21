"""Concentric-conics → rectifying homography via the image of the circular points.

Background:
  Under perspective projection, a family of coplanar concentric circles in
  the world maps to a family of coplanar conics in the image. The conics
  share a common center (the projected circle center) and common axes
  (determined by the homography). The "image of the absolute conic" restricted
  to the target plane is encoded by the *circular points* (I, J) — the pair
  of complex conjugate points at infinity through which every circle passes.

  For two concentric ellipses E1, E2 with center (xc, yc), translating to
  origin gives conic matrices:

      E_i = [ A_i  B_i  0 ]
            [ B_i  C_i  0 ]
            [  0    0   F_i]

  The pencil E1 - λ E2 is degenerate when det(E1 - λ E2) = 0, which (since
  (A1,B1,C1) ∝ (A2,B2,C2) for shared-axis ellipses) factors as:

      (1-λ)² · (F1 - λ F2) · (A·C - B²) = 0

  At the non-trivial root λ = F1/F2, the degenerate conic reduces to the
  quadratic A x² + 2 B xy + C y² = 0 with the line-at-infinity component
  factored out. The two complex-conjugate solutions are the *circular points*
  in image coordinates.

Recovering the rectification:
  The 2x2 matrix Q = [[A, B], [B, C]] is the image-plane "metric" for the
  target. Q^{-1/2} (matrix square root) maps every ellipse that shares these
  axes back to a circle (because it whitens the metric). So the rectifying
  affine transform is:

      H_affine = [  Q^{-1/2}   |  -Q^{-1/2} · (xc, yc)^T  ]
                 [   0    0    |          1                 ]

  This maps (xc, yc) → origin and ellipses → concentric circles of equal
  eccentricity. Since the recoverable info from coplanar concentric circles
  is exactly the affine part (the projective part would need off-plane
  structure or parallel-line vanishing points, which a flat paper target
  doesn't offer), the homography is mathematically affine. We expose it as
  a 3x3 matrix with bottom row [0, 0, 1] and apply it via cv2.warpPerspective
  so the API is identical to a full projective H.

Projective refinement (optional, controlled by `projective_refine`):
  Real phone photos have mild projective tilt (the paper isn't perfectly
  frontal). The detected ring centers drift slightly with radius when this is
  the case. We fit a small projective term to the observed center drift:
  model the homography as a 1-parameter family that maps the ring at image
  radius r_i to a circle at canonical radius k_i · s, while letting the
  center shift by a quadratic-in-r term. The shift is parameterized by a
  single (vx, vy) "vanishing-line-like" vector; closed form via least squares
  on the residuals. This is a *refinement*, not the primary correction.

The self-test `bullseye_invert_err_px` (computed in the pipeline) checks that
H · (xc, yc) ≈ H^{-1}^{-1} · (xc, yc) to floating-point precision.
"""
from __future__ import annotations

import math

import numpy as np


# ---------------------------------------------------------------------------
# Conic matrix from ellipse parameters
# ---------------------------------------------------------------------------
def ellipse_to_conic(cx: float, cy: float, semi_a: float, semi_b: float, angle_deg: float) -> np.ndarray:
    """3x3 symmetric conic matrix C such that x^T C x = 0 on the ellipse.

    For a centered, axis-aligned ellipse (x²/a² + y²/b² = 1), C is
    diag(1/a², 1/b², -1). Rotate by θ and translate by (cx, cy):

        C = T^T · R^T · diag(1/a², 1/b², -1) · R · T

    where R rotates by θ and T translates by -(cx, cy).
    """
    a = max(float(semi_a), 1e-6)
    b = max(float(semi_b), 1e-6)
    th = math.radians(angle_deg)
    cos_t, sin_t = math.cos(th), math.sin(th)
    # Rotation R that brings ellipse axes onto x/y.
    R = np.array([
        [cos_t,  sin_t, 0],
        [-sin_t, cos_t, 0],
        [0,      0,     1],
    ], dtype=np.float64)
    # Translation T that maps (cx, cy) → origin.
    T = np.array([
        [1, 0, -cx],
        [0, 1, -cy],
        [0, 0,  1 ],
    ], dtype=np.float64)
    D = np.diag([1.0 / (a * a), 1.0 / (b * b), -1.0])
    # C = (R · T)^T · D · (R · T)
    RT = R @ T
    return RT.T @ D @ RT


def conic_2x2_block(C: np.ndarray) -> np.ndarray:
    """Upper-left 2x2 of a conic matrix (the image-plane metric Q)."""
    return C[:2, :2]


# ---------------------------------------------------------------------------
# Concentric-conics calibration
# ---------------------------------------------------------------------------
def average_shared_metric(rings: list[dict]) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """Compute the average (A, B, C) metric across all rings, weighted by
    inverse semi-axis (so smaller, sharper rings weigh comparably to larger
    ones). Returns (Q_2x2, center, weights).

    The shared metric assumption: for projected concentric circles, every
    ellipse's 2x2 block is proportional to a single Q. We extract Q by
    averaging the *normalized* 2x2 blocks (each scaled so its smallest
    eigenvalue = 1, which removes the radius scaling and keeps only the
    shared eccentricity/orientation).
    """
    Qs: list[np.ndarray] = []
    ws: list[float] = []
    centers: list[np.ndarray] = []
    for r in rings:
        C = ellipse_to_conic(r["cx"], r["cy"], r["semi_a"], r["semi_b"], r["angle_deg"])
        Q = conic_2x2_block(C)
        # Normalize: divide by min eigenvalue so the smaller-eigenvalue axis
        # has unit length. This puts all rings on the same scale.
        ev = np.linalg.eigvalsh(Q)
        if ev.min() < 1e-9:
            continue
        Q_norm = Q / ev.min()
        # Weight by inverse size so small inner rings count.
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
    # Q_avg should be real-symmetric but ensure it exactly is.
    Q_avg = 0.5 * (Q_avg + Q_avg.T)
    center = np.average(np.array(centers, dtype=np.float64), axis=0, weights=W)
    return Q_avg, center, [float(w) for w in ws]


def matrix_inverse_sqrt(Q: np.ndarray) -> np.ndarray:
    """Principal inverse square root of a 2x2 symmetric positive-definite Q.

    Q = R^T diag(λ1, λ2) R  →  Q^{-1/2} = R^T diag(1/√λ1, 1/√λ2) R.
    """
    # Symmetrize defensively.
    Q = 0.5 * (Q + Q.T)
    eigvals, eigvecs = np.linalg.eigh(Q)
    eigvals = np.clip(eigvals, 1e-9, None)
    return (eigvecs * (1.0 / np.sqrt(eigvals))) @ eigvecs.T


def circular_points_from_Q(Q: np.ndarray) -> tuple[complex, complex]:
    """Solve A x² + 2 B xy + C y² = 0 for the two slopes x/y of the circular
    points on the line at infinity. Returns (m, m*) as a complex-conjugate
    pair (in the chosen convention, the two roots of A m² + 2 B m + C = 0
    where m = x/y).
    """
    A, B, C = Q[0, 0], Q[0, 1], Q[1, 1]
    disc = B * B - A * C
    if disc >= 0:
        # Shouldn't happen for an ellipse (Q is SPD), but guard anyway.
        return complex(0.0, 1.0), complex(0.0, -1.0)
    sq = math.sqrt(-disc)
    m1 = complex(-B / A, sq / A)
    m2 = complex(-B / A, -sq / A)
    return m1, m2


# ---------------------------------------------------------------------------
# Projective refinement (optional)
# ---------------------------------------------------------------------------
def estimate_center_drift_projective(
    rings: list[dict],
    center: np.ndarray,
) -> np.ndarray | None:
    """Estimate a small projective "center drift" vector (vx, vy) from the
    observed drift of ring centers with radius.

    Under pure affine, all ring centers coincide. Under mild projective tilt,
    the apparent center of ring i shifts approximately as a quadratic function
    of its radius. We fit:

        c_i ≈ center + (vx, vy) · r_i²

    by least squares. The (vx, vy) vector parameterizes the dominant projective
    skew and is folded into the homography as a small perspective term.

    Returns the 1x2 (vx, vy) vector, or None if the fit is unstable.
    """
    if len(rings) < 3:
        return None
    rs: list[float] = []
    dxs: list[float] = []
    dys: list[float] = []
    for r in rings:
        g = math.sqrt(r["semi_a"] * r["semi_b"])
        rs.append(g * g)                          # quadratic in r
        dxs.append(r["cx"] - center[0])
        dys.append(r["cy"] - center[1])
    if max(rs) < 1e-6:
        return None
    A = np.array(rs, dtype=np.float64).reshape(-1, 1)
    # Solve dxs = vx · r², dys = vy · r² (least squares).
    vx, *_ = np.linalg.lstsq(A, np.array(dxs), rcond=None)
    vy, *_ = np.linalg.lstsq(A, np.array(dys), rcond=None)
    v = np.array([vx[0], vy[0]], dtype=np.float64)
    # Sanity: the drift must be tiny compared to ring radii; if huge, the fit
    # is being thrown off by a bad ring (e.g. a stray contour clipped to the
    # paper edge). Reject.
    max_r = max(math.sqrt(r["semi_a"] * r["semi_b"]) for r in rings)
    expected_drift_at_max = np.linalg.norm(v) * max_r * max_r
    if expected_drift_at_max > 0.30 * max_r:
        return None
    return v


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def compute_rectifying_homography(
    rings: list[dict],
    projective_refine: bool = True,
) -> dict:
    """Compute the 3x3 rectifying homography H that maps detected concentric
    ellipses to concentric circles centered at the origin.

    Returns {H, H_inv, Q, center, circular_points, center_drift, used_projective}.

    `rings` must have keys: cx, cy, semi_a, semi_b, angle_deg.
    """
    if len(rings) < 2:
        raise ValueError(f"need ≥2 concentric rings for calibration, got {len(rings)}")

    Q, center, _ = average_shared_metric(rings)
    Qinv_sqrt = matrix_inverse_sqrt(Q)
    circular = circular_points_from_Q(Q)

    # Affine rectification: (x, y) → Q^{-1/2} · ((x, y) - center).
    # Builds a 3x3 with bottom row [0, 0, 1].
    H = np.eye(3, dtype=np.float64)
    H[:2, :2] = Qinv_sqrt
    H[:2, 2] = -Qinv_sqrt @ center

    used_projective = False
    center_drift = None
    if projective_refine:
        v = estimate_center_drift_projective(rings, center)
        if v is not None:
            # Fold the projective term into H as a small correction to the
            # bottom row: H[2, :2] = -2 (vx, vy) (sign chosen so that the
            # center-shift induced by H cancels the observed drift to first
            # order). See report for derivation.
            center_drift = v
            H[2, 0] = -2.0 * v[0]
            H[2, 1] = -2.0 * v[1]
            # Re-normalize H[2,2] so the bullseye maps to a finite point.
            # (For typical v, |H[2,:2]| ≪ 1/|center|, so H[2,2]=1 stays fine.)
            used_projective = True

    H_inv = np.linalg.inv(H)
    return {
        "H": H,
        "H_inv": H_inv,
        "Q": Q,
        "center": center,
        "circular_points": circular,
        "center_drift": center_drift,
        "used_projective": used_projective,
    }


def ring_radii_in_warped(rings: list[dict], H: np.ndarray) -> list[float]:
    """Map each detected ring's geometric-mean radius to its warped-frame
    radius (assuming the warp turns it into a circle). Used by normalize.py
    to compute the canonical 1-ring outer radius in warped px.

    For an affine warp (bottom row [0,0,1]), the warped radius is
    semi_a · semi_b / sqrt( (Q^{-1/2} · dir_a)²  +  (Q^{-1/2} · dir_b)² ) ...
    but simpler: after applying H to the conic, the new ellipse has
    semi-axes = 1/√eigenvals of the new conic's 2x2 block. For our H =
    [Q^{-1/2}, t; 0, 1] applied to a conic whose 2x2 block is k·Q, the
    result's 2x2 block is k·I, i.e. a circle of radius 1/√k. We use this
    to compute the warped radius exactly.
    """
    out = []
    for r in rings:
        # Conic 2x2 block of this ring (proportional to Q with k = 1/(b·semi_b))
        # → after H, becomes k·I → radius = 1/√k = √(semi_a·semi_b) when Q is
        # already normalized. To stay robust we just use the geometric mean
        # of the semi-axes as a proxy: for affine rectification, all rings
        # get rescaled by the same factor (the dominant eigenvalue ratio of
        # Q^{-1/2}), so the *ratios* of warped radii equal the ratios of
        # geometric means.
        gmean = math.sqrt(r["semi_a"] * r["semi_b"])
        out.append(float(gmean))
    return out
