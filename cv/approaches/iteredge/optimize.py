"""Energy function + optimizer for iteredge homography refinement.

Energy formulation (Lucas-Kanade-style image alignment):
  Variables: 8 homography params θ = [h11,h12,h13,h21,h22,h23,h31,h32]
  Constants:
    - ring potential P (distance to nearest ring-weighted edge) — in crop frame
    - warped-frame ring points W — shape (N, 2)
    - affine init params θ₀
  Residuals (length N + n_reg):
    For each ring point w_i ∈ W:
      - Map to crop frame via H(θ)⁻¹: c_i = H⁻¹ applied to w_i
      - r_i = P(c_i)      # DT = 0 on a ring edge, larger far away.
                          # Minimizing pulls the prediction toward ring edges.
    Plus regularization residuals:
      - perspective terms (h31, h32): penalize magnitude.
      - deviation from affine init: penalize drift in (h11..h23).
      - determinant must stay positive + close to det(affine_init): barrier.

Parameter bounds (scipy 'trf' method):
  - Affine params (6): ±100% deviation from init.
  - Perspective terms (2): ±1e-2 absolute.

Coarse-to-fine: at each stage we use a different `potential`:
  - Stage 1 (broadest): smoothed ring-weighted magnitude (broad basin).
  - Stages 2-4: dt_ring (exact placement).

Robust `soft_l1` loss down-weights outliers (non-ring edges that survived
the ring filter — logos with tangential strokes, hole edges, etc.).
"""
from __future__ import annotations

import math

import cv2
import numpy as np
from scipy.optimize import least_squares

from cv.approaches.iteredge.edges import enhance_ring_edges
from cv.approaches.iteredge.model import (
    apply_H_to_points,
    params_to_H,
    ring_points_warped,
    sample_potential,
)


# ---------------------------------------------------------------------------
# Residuals
# ---------------------------------------------------------------------------
def make_residual_fn(
    potential: np.ndarray,
    ring_pts_warped: np.ndarray,
    affine_init: np.ndarray,
    crop_shape: tuple[int, int],
    reg_perspective: float = 1e3,
    reg_anchor: float = 1.0,
    reg_det: float = 10.0,
    data_weight: float = 1.0,
    potential_kind: str = "dt",   # "dt" or "mag"
) -> callable:
    """Build the residuals callable for least_squares.

    potential_kind:
      "dt"  — residual = potential value (DT). Minimizing → move toward 0
              (ring edge).
      "mag" — residual = (target - potential value). Minimizing → move toward
              high magnitude (broad-basin alignment for the coarsest stage).
    """
    H, W = crop_shape
    n_pts = ring_pts_warped.shape[0]
    aff = np.asarray(affine_init, dtype=np.float64)
    H_aff = params_to_H(aff)
    det_target = float(np.linalg.det(H_aff))

    if potential_kind == "mag":
        p_max = float(np.percentile(potential, 99))
        p_offset = max(p_max * 0.6, float(potential.mean()) * 1.5)
    else:
        p_offset = 0.0  # unused for "dt"

    sqrt_dw = math.sqrt(data_weight)

    def residuals(params: np.ndarray) -> np.ndarray:
        H_mat = params_to_H(params)
        det_now = float(np.linalg.det(H_mat))
        if det_now <= 0.1 or det_now > 10.0:
            return np.full(n_pts + 9, 1e6, dtype=np.float64)
        try:
            H_inv = np.linalg.inv(H_mat)
        except np.linalg.LinAlgError:
            return np.full(n_pts + 9, 1e6, dtype=np.float64)

        crop_pts = apply_H_to_points(H_inv, ring_pts_warped)
        x = crop_pts[:, 0]
        y = crop_pts[:, 1]
        in_bounds = (x >= 0) & (x < W) & (y >= 0) & (y < H)
        vals = sample_potential(potential, crop_pts)

        if potential_kind == "mag":
            data_res = p_offset - vals
        else:
            # DT: zero on edge, large far away. We want to minimize.
            # Apply a soft saturating function so far-away predictions don't
            # dominate: residual = min(dt, cap).
            cap = 30.0
            data_res = np.minimum(vals, cap)
        # Out-of-bounds: heavy penalty (much larger than the cap).
        data_res = np.where(in_bounds, data_res, 1e3)

        persp = np.array([params[6], params[7]], dtype=np.float64)
        anchor = params[:6] - aff[:6]
        det_res = np.array([det_now - det_target], dtype=np.float64)
        lo = 0.5 * det_target
        hi = 2.0 * det_target
        barrier = 0.0
        if det_now < lo:
            barrier = (lo - det_now) * 1e3
        elif det_now > hi:
            barrier = (det_now - hi) * 1e3
        sign_res = np.array([barrier], dtype=np.float64)

        reg_res = np.concatenate([
            math.sqrt(reg_perspective) * persp,
            math.sqrt(reg_anchor) * anchor,
            math.sqrt(reg_det) * det_res,
            sign_res,
        ])
        return np.concatenate([sqrt_dw * data_res, reg_res]).astype(np.float64)

    return residuals


# ---------------------------------------------------------------------------
# Coarse-to-fine optimization
# ---------------------------------------------------------------------------
# (sigma_factor, reg_p, reg_a, reg_d, max_iters, potential_kind, data_weight)
_DEFAULT_SCHEDULE = [
    (0.55, 1e5, 200.0, 1e3, 60, "mag", 0.5),    # broad basin, strong anchor
    (0.30, 1e4, 100.0, 200.0, 60, "dt", 1.0),   # exact placement
    (0.15, 1e3, 50.0, 50.0, 50, "dt", 1.5),
    (0.08, 200.0, 20.0, 20.0, 40, "dt", 2.0),
]


def optimize_homography(
    gray_crop: np.ndarray,
    cal: dict,
    affine_init_params: np.ndarray,
    affine_M2: np.ndarray,
    affine_t: np.ndarray,
    warped_out_center: tuple[float, float],
    n_rings: int = 10,
    n_per_ring: int = 64,
    schedule: list[tuple] | None = None,
) -> dict:
    """Coarse-to-fine homography refinement.

    Returns {final_params, final_H, final_cost, n_iterations, converged, stages}.
    """
    cx, cy = cal["cx"], cal["cy"]
    s_px = cal["s_px"]
    r_bull = cal["r_bull_px"]
    ocx, ocy = warped_out_center

    if s_px <= 0 or r_bull <= 0:
        return {
            "final_params": np.asarray(affine_init_params, dtype=np.float64),
            "final_H": params_to_H(affine_init_params),
            "final_cost": float("nan"),
            "n_iterations": 0,
            "converged": False,
            "stages": [],
            "reason": "degenerate calibration",
        }

    if schedule is None:
        schedule = [(s_px * f, rp, ra, rd, mi, pk, dw)
                    for (f, rp, ra, rd, mi, pk, dw) in _DEFAULT_SCHEDULE]

    ring_pts = ring_points_warped(
        ocx=ocx, ocy=ocy,
        r_bull_warped=r_bull, s_warped=s_px,
        n_rings=n_rings, n_per_ring=n_per_ring,
    )

    aff_init = np.asarray(affine_init_params, dtype=np.float64)
    current_params = aff_init.copy()
    stages_log = []
    last_cost = float("inf")
    converged = False

    scale = np.maximum(np.abs(aff_init), np.array([0.5] * 8))
    lb = aff_init - 1.5 * scale
    ub = aff_init + 1.5 * scale
    lb[6] = -1e-2; ub[6] = 1e-2
    lb[7] = -1e-2; ub[7] = 1e-2

    for sigma, reg_p, reg_a, reg_d, max_iters, pot_kind, dw in schedule:
        emap = enhance_ring_edges(
            gray_crop, cx=cx, cy=cy, s_px=s_px, smooth_sigma=sigma,
        )
        potential = emap["mag_smooth"] if pot_kind == "mag" else emap["dt"]

        res_fn = make_residual_fn(
            potential=potential,
            ring_pts_warped=ring_pts,
            affine_init=aff_init,
            crop_shape=gray_crop.shape,
            reg_perspective=reg_p,
            reg_anchor=reg_a,
            reg_det=reg_d,
            data_weight=dw,
            potential_kind=pot_kind,
        )

        result = least_squares(
            res_fn,
            current_params,
            method="trf",
            bounds=(lb, ub),
            max_nfev=max_iters * 8,
            xtol=1e-12, ftol=1e-12, gtol=1e-12,
            loss="soft_l1", f_scale=1.0,
        )
        cost = float(result.cost)
        H_now = params_to_H(result.x)
        det = float(np.linalg.det(H_now))
        if not (0.1 < det < 10.0):
            stages_log.append({
                "sigma": sigma, "cost": cost, "nfev": int(result.nfev),
                "det": det, "rejected": True, "reason": f"degenerate det={det:.2e}",
                "pot": pot_kind,
            })
            continue

        current_params = result.x
        last_cost = cost
        converged = bool(result.success)
        stages_log.append({
            "sigma": sigma, "cost": cost, "nfev": int(result.nfev),
            "det": det, "success": bool(result.success), "pot": pot_kind,
        })

    n_total_iters = sum(int(s.get("nfev", 0)) for s in stages_log)

    # Final safety check: compute the data-only residual (no regularization)
    # for both the affine init and the optimized result on the sharpest
    # potential. If the optimizer made things worse, revert to the init.
    emap_final = enhance_ring_edges(
        gray_crop, cx=cx, cy=cy, s_px=s_px, smooth_sigma=s_px * 0.08,
    )
    final_pot = emap_final["dt"]

    def data_residual(params):
        H_mat = params_to_H(params)
        det_now = float(np.linalg.det(H_mat))
        if det_now <= 0.1 or det_now > 10.0:
            return float("inf")
        H_inv = np.linalg.inv(H_mat)
        crop_pts = apply_H_to_points(H_inv, ring_pts)
        x = crop_pts[:, 0]
        y = crop_pts[:, 1]
        in_bounds = (x >= 0) & (x < gray_crop.shape[1]) & (y >= 0) & (y < gray_crop.shape[0])
        vals = sample_potential(final_pot, crop_pts)
        vals = np.where(in_bounds, np.minimum(vals, 30.0), 30.0)
        return float(np.mean(vals))

    init_score = data_residual(aff_init)
    final_score = data_residual(current_params)

    if final_score > init_score:
        # Optimizer made things worse; revert.
        stages_log.append({
            "sigma": 0.0, "cost": 0.0, "nfev": 0, "det": float(np.linalg.det(params_to_H(aff_init))),
            "success": True, "pot": "revert", "reason": f"init_score={init_score:.2f} < final_score={final_score:.2f}",
        })
        return {
            "final_params": aff_init,
            "final_H": params_to_H(aff_init),
            "final_cost": float(init_score),
            "n_iterations": n_total_iters,
            "converged": True,
            "stages": stages_log,
            "reverted_to_init": True,
            "init_data_score": init_score,
            "opt_data_score": final_score,
        }

    return {
        "final_params": current_params,
        "final_H": params_to_H(current_params),
        "final_cost": last_cost,
        "n_iterations": n_total_iters,
        "converged": converged,
        "stages": stages_log,
        "reverted_to_init": False,
        "init_data_score": init_score,
        "opt_data_score": final_score,
    }
