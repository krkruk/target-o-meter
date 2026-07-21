"""8-DOF differential homography refinement with stage-by-stage callback.

This reimplements `cv.approaches.iteredge.optimize_homography` with two
additions:

  1. An optional `stage_callback` invoked after each coarse-to-fine stage, so
     the fused pipeline can render intermediate projection PNGs (one per
     stage) for overshoot tracking.
  2. An inlined copy of `make_residual_fn` (instead of importing it from
     iteredge) with a corrected residual length on the degenerate-det early
     return. The iteredge original returns `n_pts+9` on the degenerate path
     but `n_pts+10` on the success path (concat of persp[2] + anchor[6] +
     det_res[1] + sign_res[1]); scipy raises a shape-mismatch ValueError
     when the optimizer wanders into the degenerate region mid-iteration
     (e.g. image 21 with multiring's near-full-image crop). We do NOT
     modify iteredge source — we copy the function and fix the off-by-one.

The math, schedule, regularizers, parameterization, and bounds are identical
to iteredge's — see `cv/approaches/iteredge/optimize.py` for the full
derivation.
"""
from __future__ import annotations

import math
from typing import Callable

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
# Residual function (inlined from iteredge.optimize.make_residual_fn with
# the off-by-one on the degenerate-det early return fixed).
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
    potential_kind: str = "dt",
) -> Callable[[np.ndarray], np.ndarray]:
    """Build the residuals callable for least_squares.

    Residual vector shape: `n_pts + 10` (data + persp[2] + anchor[6] +
    det_res[1] + sign_res[1]). All early-return paths use the same length.
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
        p_offset = 0.0

    sqrt_dw = math.sqrt(data_weight)
    out_len = n_pts + 10  # fixed across all return paths

    def residuals(params: np.ndarray) -> np.ndarray:
        H_mat = params_to_H(params)
        det_now = float(np.linalg.det(H_mat))
        if det_now <= 0.1 or det_now > 10.0:
            return np.full(out_len, 1e6, dtype=np.float64)
        try:
            H_inv = np.linalg.inv(H_mat)
        except np.linalg.LinAlgError:
            return np.full(out_len, 1e6, dtype=np.float64)

        crop_pts = apply_H_to_points(H_inv, ring_pts_warped)
        x = crop_pts[:, 0]
        y = crop_pts[:, 1]
        in_bounds = (x >= 0) & (x < W) & (y >= 0) & (y < H)
        vals = sample_potential(potential, crop_pts)

        if potential_kind == "mag":
            data_res = p_offset - vals
        else:
            cap = 30.0
            data_res = np.minimum(vals, cap)
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


# Same schedule iteredge uses (sigma_factor, reg_p, reg_a, reg_d, max_iters,
# potential_kind, data_weight).
DEFAULT_SCHEDULE = [
    (0.55, 1e5, 200.0, 1e3, 60, "mag", 0.5),    # broad basin, strong anchor
    (0.30, 1e4, 100.0, 200.0, 60, "dt", 1.0),   # exact placement
    (0.15, 1e3, 50.0, 50.0, 50, "dt", 1.5),
    (0.08, 200.0, 20.0, 20.0, 40, "dt", 2.0),
]


StageCallback = Callable[[int, np.ndarray, np.ndarray, np.ndarray, dict], None]


def refine_homography(
    gray_crop: np.ndarray,
    cal: dict,
    affine_init_params: np.ndarray,
    affine_M2: np.ndarray,
    affine_t: np.ndarray,
    warped_out_center: tuple[float, float],
    s_warped: float,
    r_bull_warped: float,
    n_rings: int = 10,
    n_per_ring: int = 64,
    schedule: list[tuple] | None = None,
    stage_callback: StageCallback | None = None,
) -> dict:
    """Coarse-to-fine 8-DOF homography refinement.

    Args:
      cal: dict with crop-frame `cx`, `cy`, `s_px`, `r_bull_px`. The crop-
        frame `s_px` is used by `enhance_ring_edges` for blur/falloff; the
        crop-frame values are NOT used for ring generation (see below).
      s_warped, r_bull_warped: WARPED-frame ring spacing and inner bullseye
        radius. Used to generate `ring_points_warped` — the predicted ring
        sample points. These MUST be computed from the actual detected rings
        transformed through `affine_init_params`'s H, NOT just copied from
        cal (which is what iteredge does — works for it because blob_detect's
        affine init is near-identity, but breaks for multiring's Q^{-1/2}
        which rescales rings significantly).
      stage_callback: optional, called after each stage with the current H.

    Returns {final_params, final_H, final_cost, n_iterations, converged,
            stages, reverted_to_init, init_data_score, opt_data_score}.
    """
    cx, cy = cal["cx"], cal["cy"]
    s_px_crop = cal["s_px"]  # crop-frame, for enhance_ring_edges
    ocx, ocy = warped_out_center

    if s_px_crop <= 0 or s_warped <= 0 or r_bull_warped <= 0:
        return {
            "final_params": np.asarray(affine_init_params, dtype=np.float64),
            "final_H": params_to_H(affine_init_params),
            "final_cost": float("nan"),
            "n_iterations": 0,
            "converged": False,
            "stages": [],
            "reverted_to_init": False,
            "reason": "degenerate calibration",
            "init_data_score": float("nan"),
            "opt_data_score": float("nan"),
        }

    if schedule is None:
        # sigma is a blur amount in CROP px → scale by crop-frame s_px
        schedule = [(s_px_crop * f, rp, ra, rd, mi, pk, dw)
                    for (f, rp, ra, rd, mi, pk, dw) in DEFAULT_SCHEDULE]

    # Rings are generated in WARPED frame with WARPED-frame radii.
    ring_pts = ring_points_warped(
        ocx=ocx, ocy=ocy,
        r_bull_warped=r_bull_warped, s_warped=s_warped,
        n_rings=n_rings, n_per_ring=n_per_ring,
    )

    aff_init = np.asarray(affine_init_params, dtype=np.float64)
    current_params = aff_init.copy()
    stages_log: list[dict] = []
    last_cost = float("inf")
    converged = False

    scale = np.maximum(np.abs(aff_init), np.array([0.5] * 8))
    lb = aff_init - 1.5 * scale
    ub = aff_init + 1.5 * scale
    lb[6] = -1e-2; ub[6] = 1e-2
    lb[7] = -1e-2; ub[7] = 1e-2

    # Emit a stage-0 callback for the initial state (before any optimization)
    # so the pipeline can render the "before" projection.
    if stage_callback is not None:
        stage_callback(
            stage_idx=0,
            current_params=aff_init.copy(),
            current_H=params_to_H(aff_init),
            potential=None,
            info={"sigma": 0.0, "pot_kind": "init", "cost": float("nan"),
                  "nfev": 0, "det": float(np.linalg.det(params_to_H(aff_init)))},
        )

    for stage_idx, (sigma, reg_p, reg_a, reg_d, max_iters, pot_kind, dw) in enumerate(schedule, start=1):
        emap = enhance_ring_edges(
            gray_crop, cx=cx, cy=cy, s_px=s_px_crop, smooth_sigma=sigma,
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

        stage_entry = {
            "sigma": float(sigma), "cost": cost, "nfev": int(result.nfev),
            "det": det, "success": bool(result.success), "pot": pot_kind,
        }

        if not (0.1 < det < 10.0):
            stage_entry.update({"rejected": True,
                                "reason": f"degenerate det={det:.2e}"})
            stages_log.append(stage_entry)
            if stage_callback is not None:
                stage_callback(stage_idx, current_params.copy(),
                               params_to_H(current_params), potential, stage_entry)
            continue

        current_params = result.x
        last_cost = cost
        converged = bool(result.success)
        stages_log.append(stage_entry)

        if stage_callback is not None:
            stage_callback(stage_idx, current_params.copy(),
                           H_now, potential, stage_entry)

    n_total_iters = sum(int(s.get("nfev", 0)) for s in stages_log)

    # Final safety check — revert to init if optimization made things worse
    # (data-only score on the sharpest DT).
    emap_final = enhance_ring_edges(
        gray_crop, cx=cx, cy=cy, s_px=s_px_crop, smooth_sigma=s_px_crop * 0.08,
    )
    final_pot = emap_final["dt"]

    def data_residual(params: np.ndarray) -> float:
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
        stages_log.append({
            "sigma": 0.0, "cost": 0.0, "nfev": 0,
            "det": float(np.linalg.det(params_to_H(aff_init))),
            "success": True, "pot": "revert",
            "reason": f"init_score={init_score:.2f} < final_score={final_score:.2f}",
        })
        if stage_callback is not None:
            stage_callback(
                stage_idx=len(schedule) + 1,
                current_params=aff_init.copy(),
                current_H=params_to_H(aff_init),
                potential=final_pot,
                info={"sigma": 0.0, "pot_kind": "revert", "cost": float(init_score),
                      "nfev": 0, "det": float(np.linalg.det(params_to_H(aff_init))),
                      "reverted_to_init": True},
            )
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
