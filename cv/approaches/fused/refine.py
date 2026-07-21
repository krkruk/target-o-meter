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
# Module-level constants (must be defined before make_residual_fn since the
# latter uses some as default arg values).
# ---------------------------------------------------------------------------

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

# Layered orthogonality defenses (see refine_homography docstring).
SKIP_REFINE_ECC_THRESHOLD = 1.02     # below this → return init unchanged
AFFINE_LOCK_ECC_THRESHOLD = 1.10     # below this → lock affine, refine only perspective

# Affine bounds when refinement is allowed (ecc >= AFFINE_LOCK_ECC_THRESHOLD).
# bounds = AFFINE_BOUND_BASE × max(1.0, (ecc - 1.0) × 10)
# For ecc=1.20 → factor=2.0 → bounds = ±0.20 × |init|
# For ecc=1.50 → factor=5.0 → bounds = ±0.50 × |init|
AFFINE_BOUND_BASE = 0.10

# SV-ratio penalty (in residual). Soft penalty when M2's singular-value
# ratio exceeds this threshold. Drives the optimizer away from anisotropic
# M2 even when bounds allow some movement.
SV_RATIO_THRESHOLD = 1.05
SV_RATIO_WEIGHT = 1e3

# Post-refinement safety gates.
CORNER_RATIO_ABS_THRESHOLD = 3.0
CORNER_RATIO_RELATIVE_FACTOR = 2.0
M2_ANISO_GATE_THRESHOLD = 1.10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sv_ratio(M2: np.ndarray) -> float:
    """Singular-value ratio SV_max / SV_min of 2x2 M2 (1.0 = isotropic)."""
    try:
        s = np.linalg.svd(M2, compute_uv=False)
        if s[1] < 1e-9:
            return float("inf")
        return float(s[0] / s[1])
    except np.linalg.LinAlgError:
        return float("inf")


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
    enable_sv_penalty: bool = True,
    sv_ratio_threshold: float = SV_RATIO_THRESHOLD,
    sv_ratio_weight: float = SV_RATIO_WEIGHT,
) -> Callable[[np.ndarray], np.ndarray]:
    """Build the residuals callable for least_squares.

    Residual vector shape: `n_pts + 10 + (1 if sv_penalty else 0)`.
    Components (all return paths use the same length):
      - data: n_pts
      - perspective reg: 2 (h31, h32)
      - anchor reg: 6 (params[:6] - aff[:6])
      - det reg: 1 (det - det_target)
      - det barrier: 1 (sign-respecting soft barrier outside [0.5, 2]×det_target)
      - sv penalty (optional): 1 (max(0, SV_max/SV_min - threshold) × weight)
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
    sqrt_sv_w = math.sqrt(sv_ratio_weight) if enable_sv_penalty else 0.0
    out_len = n_pts + 10 + (1 if enable_sv_penalty else 0)

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

        reg_res = [
            math.sqrt(reg_perspective) * persp,
            math.sqrt(reg_anchor) * anchor,
            math.sqrt(reg_det) * det_res,
            sign_res,
        ]
        if enable_sv_penalty:
            sv_ratio = _sv_ratio(H_mat[:2, :2])
            sv_pen = max(0.0, sv_ratio - sv_ratio_threshold)
            reg_res.append(np.array([sqrt_sv_w * sv_pen], dtype=np.float64))
        reg_arr = np.concatenate(reg_res)
        return np.concatenate([sqrt_dw * data_res, reg_arr]).astype(np.float64)

    return residuals


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
    perspective_bound: float = DEFAULT_PERSPECTIVE_BOUND,
    edge_band_mask: np.ndarray | None = None,
    corner_gate_enable: bool = True,
    mean_ring_eccentricity: float = 1.0,
    skip_ecc_threshold: float = SKIP_REFINE_ECC_THRESHOLD,
    affine_lock_ecc_threshold: float = AFFINE_LOCK_ECC_THRESHOLD,
    affine_bound_base: float = AFFINE_BOUND_BASE,
    enable_sv_penalty: bool = True,
    sv_ratio_threshold: float = SV_RATIO_THRESHOLD,
    sv_ratio_weight: float = SV_RATIO_WEIGHT,
    enable_m2_aniso_gate: bool = True,
    m2_aniso_gate_threshold: float = M2_ANISO_GATE_THRESHOLD,
) -> dict:
    """Coarse-to-fine 8-DOF homography refinement with layered orthogonality
    defenses.

    Defense layers (applied in order):
      1. SKIP if `mean_ring_eccentricity < skip_ecc_threshold` (1.02) →
         return multiring's affine H_init unchanged. For near-frontal
         sources, the analytical circular-points rectifier is provably
         optimal; the optimizer can only add noise.
      2. LOCK AFFINE if `ecc < affine_lock_ecc_threshold` (1.10) → set
         lb[:6] = ub[:6] = aff_init[:6]. Optimizer only refines h31, h32
         (2 DOF). Prevents the M2 anisotropy drift that causes visible
         elongation on orthogonal sources (image 1 root cause).
      3. SCALE BOUNDS by eccentricity if ecc ≥ 1.10 → allows genuine affine
         refinement for tilted sources, bounded proportionally to how
         tilted the source is.
      4. SV-RATIO PENALTY in residual → soft penalty on M2's singular-value
         ratio exceeding 1.05. Drives optimizer away from anisotropic M2
         even when bounds allow movement.
      5. POST-REFINEMENT GATES:
         - Corner-radius-ratio gate (catastrophic perspective distortion)
         - M2 anisotropy gate (visible affine elongation)

    Args (in addition to the iteredge-compatible ones):
      mean_ring_eccentricity: averaged semi_a/semi_b from multiring's
        detected rings. 1.0 = perfectly circular. Drives defense layers
        1-3.
      edge_band_mask: optional boolean mask; when provided, DT is recomputed
        on Canny edges ANDed with this mask.
      perspective_bound: max |h31|, |h32| (default ±1e-4).
      enable_sv_penalty: include SV-ratio penalty in residual (default True).
      enable_m2_aniso_gate: post-refinement M2 anisotropy check (default True).

    Returns {final_params, final_H, final_cost, n_iterations, converged,
            stages, reverted_to_init, revert_reason, init_data_score,
            opt_data_score, corner_ratio_init, corner_ratio_final,
            corner_ratio_gate, m2_aniso_init, m2_aniso_final,
            m2_aniso_gate, mean_ring_eccentricity, defense_layer}.
    """
    cx, cy = cal["cx"], cal["cy"]
    s_px_crop = cal["s_px"]  # crop-frame, for enhance_ring_edges
    ocx, ocy = warped_out_center

    # ----- Defense Layer 1: skip refinement entirely for orthogonal sources
    if mean_ring_eccentricity < skip_ecc_threshold:
        skip_reason = (f"skip_refinement: ecc={mean_ring_eccentricity:.3f} < "
                       f"threshold={skip_ecc_threshold}")
        if stage_callback is not None:
            stage_callback(
                stage_idx=0,
                current_params=np.asarray(affine_init_params, dtype=np.float64).copy(),
                current_H=params_to_H(affine_init_params),
                potential=None,
                info={"sigma": 0.0, "pot_kind": "skip", "cost": float("nan"),
                      "nfev": 0, "det": float(np.linalg.det(params_to_H(affine_init_params))),
                      "reverted_to_init": True, "reason": skip_reason},
            )
        return {
            "final_params": np.asarray(affine_init_params, dtype=np.float64),
            "final_H": params_to_H(affine_init_params),
            "final_cost": float("nan"),
            "n_iterations": 0,
            "converged": True,
            "stages": [],
            "reverted_to_init": True,
            "revert_reason": skip_reason,
            "init_data_score": float("nan"),
            "opt_data_score": float("nan"),
            "corner_ratio_init": None,
            "corner_ratio_final": None,
            "corner_ratio_gate": None,
            "m2_aniso_init": None,
            "m2_aniso_final": None,
            "m2_aniso_gate": None,
            "mean_ring_eccentricity": float(mean_ring_eccentricity),
            "defense_layer": "skip",
        }

    if s_px_crop <= 0 or s_warped <= 0 or r_bull_warped <= 0:
        return {
            "final_params": np.asarray(affine_init_params, dtype=np.float64),
            "final_H": params_to_H(affine_init_params),
            "final_cost": float("nan"),
            "n_iterations": 0,
            "converged": False,
            "stages": [],
            "reverted_to_init": False,
            "revert_reason": "degenerate calibration",
            "init_data_score": float("nan"),
            "opt_data_score": float("nan"),
            "corner_ratio_init": None,
            "corner_ratio_final": None,
            "corner_ratio_gate": None,
            "m2_aniso_init": None,
            "m2_aniso_final": None,
            "m2_aniso_gate": None,
            "mean_ring_eccentricity": float(mean_ring_eccentricity),
            "defense_layer": "degenerate",
        }

    if schedule is None:
        schedule = [(s_px_crop * f, rp, ra, rd, mi, pk, dw)
                    for (f, rp, ra, rd, mi, pk, dw) in DEFAULT_SCHEDULE]

    # Rings in WARPED frame with WARPED-frame radii.
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

    # ----- Defense Layer 2/3: parameter bounds -----
    if mean_ring_eccentricity < affine_lock_ecc_threshold:
        # Layer 2: LOCK AFFINE — refine only h31, h32 (2 DOF). scipy requires
        # lb < ub strictly, so add a tiny epsilon (effectively locks the param).
        eps = 1e-9
        lb = aff_init.copy() - eps
        ub = aff_init.copy() + eps
        lb[6] = -perspective_bound; ub[6] = perspective_bound
        lb[7] = -perspective_bound; ub[7] = perspective_bound
        defense_layer = "lock_affine"
    else:
        # Layer 3: SCALE BOUNDS by eccentricity for genuinely tilted sources
        ecc_factor = max(1.0, (mean_ring_eccentricity - 1.0) * 10.0)
        bound_factor = affine_bound_base * ecc_factor
        scale = np.maximum(np.abs(aff_init), np.array([0.5] * 8))
        lb = aff_init - bound_factor * scale
        ub = aff_init + bound_factor * scale
        lb[6] = -perspective_bound; ub[6] = perspective_bound
        lb[7] = -perspective_bound; ub[7] = perspective_bound
        defense_layer = f"ecc_scaled(bf={bound_factor:.3f})"

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
            enable_sv_penalty=enable_sv_penalty,
            sv_ratio_threshold=sv_ratio_threshold,
            sv_ratio_weight=sv_ratio_weight,
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

    # M2 anisotropy gate: catches visible affine elongation that the corner-
    # ratio gate might miss (affine anisotropy scales the whole image uniformly
    # without changing the corner asymmetry ratio). Triggers when M2's SV
    # ratio exceeds threshold (default 1.10 = max 10% elongation).
    if enable_m2_aniso_gate:
        m2_aniso_init = _sv_ratio(params_to_H(aff_init)[:2, :2])
        m2_aniso_final = _sv_ratio(params_to_H(current_params)[:2, :2])
        m2_gate_triggered = (
            not math.isfinite(m2_aniso_final)
            or m2_aniso_final > m2_aniso_gate_threshold
        )
    else:
        m2_aniso_init = None
        m2_aniso_final = None
        m2_gate_triggered = False

    revert_reason = None
    if corner_gate_triggered:
        revert_reason = (
            f"corner_ratio_gate: final={corner_ratio_final:.2f} > "
            f"threshold={corner_gate_threshold:.2f} (init={corner_ratio_init:.2f})"
        )
    elif m2_gate_triggered:
        revert_reason = (
            f"m2_aniso_gate: final={m2_aniso_final:.3f} > "
            f"threshold={m2_aniso_gate_threshold:.3f} (init={m2_aniso_init:.3f})"
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
            "m2_aniso_init": m2_aniso_init,
            "m2_aniso_final": m2_aniso_final,
            "m2_aniso_gate": m2_aniso_gate_threshold if enable_m2_aniso_gate else None,
            "mean_ring_eccentricity": float(mean_ring_eccentricity),
            "defense_layer": defense_layer,
        }

    return {
        "final_params": current_params,
        "final_H": params_to_H(current_params),
        "final_cost": last_cost,
        "n_iterations": n_total_iters,
        "converged": converged,
        "stages": stages_log,
        "reverted_to_init": False,
        "revert_reason": None,
        "init_data_score": init_score,
        "opt_data_score": final_score,
        "corner_ratio_init": corner_ratio_init,
        "corner_ratio_final": corner_ratio_final,
        "corner_ratio_gate": corner_gate_threshold,
        "m2_aniso_init": m2_aniso_init,
        "m2_aniso_final": m2_aniso_final,
        "m2_aniso_gate": m2_aniso_gate_threshold if enable_m2_aniso_gate else None,
        "mean_ring_eccentricity": float(mean_ring_eccentricity),
        "defense_layer": defense_layer,
    }
