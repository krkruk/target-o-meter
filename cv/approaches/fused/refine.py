"""8-DOF differential homography refinement with stage-by-stage callback.

This reimplements `cv.approaches.iteredge.optimize_homography` with several
additions tuned for the fused pipeline:

  1. Optional `stage_callback` invoked after each coarse-to-fine stage, so
     the fused pipeline can render intermediate projection PNGs (one per
     stage) for overshoot tracking.
  2. Inlined copy of `make_residual_fn` (instead of importing from iteredge)
     with a corrected residual length on the degenerate-det early return
     (iteredge returns n_pts+9 there but n_pts+10 on success — scipy raises
     a shape-mismatch when the optimizer wanders into the degenerate region).
  3. Configurable `perspective_bound` (default ±1e-4, was iteredge's ±1e-2).
     The iteredge bound was sized for blob_detect's near-identity affine
     init; multiring's Q⁻¹ᐟ² H_init places the bullseye far from crop
     corners, so even "small" h31 values like 6.8e-4 cause the w-factor at
     crop corners to flip through zero (image 1 root cause).
  4. `reg_perspective_multiplier` (default 10×) raises every stage's
     perspective regularization. Belt-and-suspenders with the tightened
     bounds.
  5. Optional `edge_band_mask` (elliptical band around multiring's detected
     rings). When provided, the DT is recomputed on band-filtered Canny
     edges — only edges near detected ring strokes drive the optimizer.
     Prevents overfitting to digit edges, hole edges, background clutter.
  6. Post-refinement corner-radius-ratio gate. Reverts to init if the
     corner-radius asymmetry under H_opt exceeds threshold (catches
     catastrophic perspective distortion that data-score alone misses).

The math, schedule, parameterization, and bounds (other than the perspective
tightening) are identical to iteredge's — see
`cv/approaches/iteredge/optimize.py` for the full derivation.
"""
from __future__ import annotations

import math
from typing import Callable

import cv2
import numpy as np
from scipy.optimize import least_squares

from cv.approaches.iteredge.edges import edge_distance_transform, enhance_ring_edges
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


# Schedule (sigma_factor, reg_p, reg_a, reg_d, max_iters, potential_kind, data_weight).
# reg_perspective (2nd value) is 10× iteredge's defaults — see module docstring #4.
DEFAULT_SCHEDULE = [
    (0.55, 1e6, 200.0, 1e3, 60, "mag", 0.5),    # broad basin, strong anchor
    (0.30, 1e5, 100.0, 200.0, 60, "dt", 1.0),   # exact placement
    (0.15, 1e4, 50.0, 50.0, 50, "dt", 1.5),
    (0.08, 2e3, 20.0, 20.0, 40, "dt", 2.0),
]


# Default perspective bound. Iteredge uses ±1e-2 (sized for near-identity
# affine init); we use ±1e-4 because multiring's H_init can place the
# bullseye far from crop corners, amplifying h31/h32's effect on the
# w-factor at those corners.
DEFAULT_PERSPECTIVE_BOUND = 1e-4

# Post-refinement corner-radius-ratio gate. If the asymmetry of warped
# corner radii under H_opt exceeds max(CORNER_RATIO_ABS_THRESHOLD,
# CORNER_RATIO_RELATIVE_FACTOR × init_ratio), revert to init.
# For reference: a healthy affine warp on a centered bullseye has ratio ~1.5-2.5.
# Catastrophic perspective distortion pushes it past 10-30.
CORNER_RATIO_ABS_THRESHOLD = 3.0
CORNER_RATIO_RELATIVE_FACTOR = 2.0


StageCallback = Callable[[int, np.ndarray, np.ndarray, np.ndarray, dict], None]


def _corner_radius_ratio(
    H: np.ndarray, crop_shape: tuple[int, int], cx: float, cy: float,
) -> float:
    """Asymmetry of warped corner radii under H, relative to bullseye.

    Returns max(corner_radii) / min(corner_radii). A healthy affine warp
    produces ~1.5-2.5; a warp with degenerate perspective at the corners
    produces >10 (image 1 reaches 36.6).
    """
    h, w = crop_shape
    corners = np.array([[0, 0], [w, 0], [0, h], [w, h]], dtype=np.float64)
    bull_homog = H @ np.array([cx, cy, 1.0], dtype=np.float64)
    if abs(bull_homog[2]) < 1e-12:
        return float("inf")
    bull_xy = bull_homog[:2] / bull_homog[2]
    homog = np.hstack([corners, np.ones((4, 1))])
    projected = (H @ homog.T).T
    if np.any(np.abs(projected[:, 2]) < 1e-12):
        return float("inf")
    projected = projected[:, :2] / projected[:, 2:3]
    rel = projected - bull_xy.reshape(1, 2)
    radii = np.linalg.norm(rel, axis=1)
    if radii.min() < 1.0:
        return float("inf")
    return float(radii.max() / radii.min())


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
    perspective_bound: float = DEFAULT_PERSPECTIVE_BOUND,
    edge_band_mask: np.ndarray | None = None,
    corner_gate_enable: bool = True,
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
        cal.
      perspective_bound: max |h31|, |h32| allowed (default ±1e-4, was
        iteredge's ±1e-2). Set smaller (e.g. 1e-5) for orthogonal images.
      edge_band_mask: optional boolean mask of same shape as `gray_crop`.
        When provided, the DT is recomputed on Canny edges ANDed with this
        mask — keeps only edges that fall on/near detected ring ellipses.
      corner_gate_enable: when True (default), revert to init if the
        post-refinement corner-radius ratio exceeds the threshold.

    Returns {final_params, final_H, final_cost, n_iterations, converged,
            stages, reverted_to_init, init_data_score, opt_data_score,
            corner_ratio_init, corner_ratio_final, corner_ratio_gate}.
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
            "corner_ratio_init": None,
            "corner_ratio_final": None,
            "corner_ratio_gate": None,
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
    lb[6] = -perspective_bound; ub[6] = perspective_bound
    lb[7] = -perspective_bound; ub[7] = perspective_bound

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
        # Apply radial-band filter: keep only Canny edges within `edge_band_mask`,
        # then recompute DT. Prevents overfitting to digit/hole/background edges.
        # mag_smooth (coarsest stage) is NOT filtered — it's a broad-basin signal.
        if edge_band_mask is not None and pot_kind == "dt":
            canny_filtered = (emap["canny"] > 0) & edge_band_mask
            canny_filtered_uint8 = (canny_filtered.astype(np.uint8)) * 255
            emap["dt"] = edge_distance_transform(canny_filtered_uint8)
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
    if edge_band_mask is not None:
        canny_filtered = (emap_final["canny"] > 0) & edge_band_mask
        canny_filtered_uint8 = (canny_filtered.astype(np.uint8)) * 255
        final_pot = edge_distance_transform(canny_filtered_uint8)

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

    # Corner-radius-ratio gate: catches catastrophic perspective distortion
    # that the data-score check misses (the data score is computed on ring
    # points near the bullseye, where perspective is benign; the corners
    # are far away and can be distorted without affecting the data score).
    if corner_gate_enable:
        corner_ratio_init = _corner_radius_ratio(
            params_to_H(aff_init), gray_crop.shape, cx, cy,
        )
        corner_ratio_final = _corner_radius_ratio(
            params_to_H(current_params), gray_crop.shape, cx, cy,
        )
        corner_gate_threshold = max(
            CORNER_RATIO_ABS_THRESHOLD,
            CORNER_RATIO_RELATIVE_FACTOR * corner_ratio_init,
        )
        corner_gate_triggered = (
            not math.isfinite(corner_ratio_final)
            or corner_ratio_final > corner_gate_threshold
        )
    else:
        corner_ratio_init = None
        corner_ratio_final = None
        corner_gate_threshold = None
        corner_gate_triggered = False

    revert_reason = None
    if corner_gate_triggered:
        revert_reason = (
            f"corner_ratio_gate: final={corner_ratio_final:.2f} > "
            f"threshold={corner_gate_threshold:.2f} (init={corner_ratio_init:.2f})"
        )
    elif final_score > init_score:
        revert_reason = (
            f"data_score: init={init_score:.2f} < final={final_score:.2f}"
        )

    if revert_reason is not None:
        stages_log.append({
            "sigma": 0.0, "cost": 0.0, "nfev": 0,
            "det": float(np.linalg.det(params_to_H(aff_init))),
            "success": True, "pot": "revert",
            "reason": revert_reason,
        })
        if stage_callback is not None:
            stage_callback(
                stage_idx=len(schedule) + 1,
                current_params=aff_init.copy(),
                current_H=params_to_H(aff_init),
                potential=final_pot,
                info={"sigma": 0.0, "pot_kind": "revert", "cost": float(init_score),
                      "nfev": 0, "det": float(np.linalg.det(params_to_H(aff_init))),
                      "reverted_to_init": True, "reason": revert_reason},
            )
        return {
            "final_params": aff_init,
            "final_H": params_to_H(aff_init),
            "final_cost": float(init_score),
            "n_iterations": n_total_iters,
            "converged": True,
            "stages": stages_log,
            "reverted_to_init": True,
            "revert_reason": revert_reason,
            "init_data_score": init_score,
            "opt_data_score": final_score,
            "corner_ratio_init": corner_ratio_init,
            "corner_ratio_final": corner_ratio_final,
            "corner_ratio_gate": corner_gate_threshold,
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
        "corner_ratio_init": corner_ratio_init,
        "corner_ratio_final": corner_ratio_final,
        "corner_ratio_gate": corner_gate_threshold,
    }
