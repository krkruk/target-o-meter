"""Stage 5 — 8-DOF homography refinement with the 5-layer orthogonality defense.

Ported verbatim from ``cv/approaches/fused/refine.py`` (616 LOC at commit
76f6fc4). The inlined ``make_residual_fn`` (with iteredge's off-by-one +
crop/warped-frame bugs already fixed) lives as a private method here.

ALL load-bearing constants travel here per plan §Critical Implementation Details
(constants are not centralized): ``DEFAULT_SCHEDULE``,
``DEFAULT_PERSPECTIVE_BOUND``, ``SKIP_REFINE_ECC_THRESHOLD``,
``AFFINE_LOCK_ECC_THRESHOLD``, ``AFFINE_BOUND_BASE``, ``SV_RATIO_*``,
``CORNER_RATIO_*``, ``M2_ANISO_GATE_THRESHOLD``.

Math is lifted as-is into class methods and module private helpers; only
structure changes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy.optimize import least_squares

from src.domains.vision.geometry.calibration import Calibration
from src.domains.vision.geometry.edge_potential import EdgePotential
from src.domains.vision.geometry.homography_model import HomographyModel


# Schedule (sigma_factor, reg_p, reg_a, reg_d, max_iters, potential_kind, data_weight).
# reg_perspective (2nd value) is 10× iteredge's defaults — see cv/ fused/refine.py #4.
DEFAULT_SCHEDULE = [
    (0.55, 1e6, 200.0, 1e3, 60, "mag", 0.5),    # broad basin, strong anchor
    (0.30, 1e5, 100.0, 200.0, 60, "dt", 1.0),   # exact placement
    (0.15, 1e4, 50.0, 50.0, 50, "dt", 1.5),
    (0.08, 2e3, 20.0, 20.0, 40, "dt", 2.0),
]

# Default perspective bound. Iteredge uses ±1e-2 (sized for near-identity
# affine init); ±1e-4 because multiring's H_init can place the bullseye far
# from crop corners, amplifying h31/h32's effect on the w-factor at corners.
DEFAULT_PERSPECTIVE_BOUND = 1e-4

# Layered orthogonality defenses (see FusedHomographyRefiner.refine docstring).
SKIP_REFINE_ECC_THRESHOLD = 1.02     # below this → return init unchanged
AFFINE_LOCK_ECC_THRESHOLD = 1.10     # below this → lock affine, refine only perspective

# Affine bounds when refinement is allowed (ecc >= AFFINE_LOCK_ECC_THRESHOLD).
AFFINE_BOUND_BASE = 0.10

# SV-ratio penalty (soft penalty in residual when M2's SV ratio exceeds this).
SV_RATIO_THRESHOLD = 1.05
SV_RATIO_WEIGHT = 1e3

# Post-refinement safety gates.
CORNER_RATIO_ABS_THRESHOLD = 3.0
CORNER_RATIO_RELATIVE_FACTOR = 2.0
M2_ANISO_GATE_THRESHOLD = 1.10


@dataclass
class RefinementResult:
    """Shape returned by ``FusedHomographyRefiner.refine``. Fields mirror the
    cv/ return dict verbatim so the pipeline reads them unchanged."""

    final_params: np.ndarray
    final_H: np.ndarray
    final_cost: float
    n_iterations: int
    converged: bool
    stages: list[dict]
    reverted_to_init: bool
    revert_reason: str | None
    init_data_score: float
    opt_data_score: float
    corner_ratio_init: float | None
    corner_ratio_final: float | None
    corner_ratio_gate: float | None
    m2_aniso_init: float | None
    m2_aniso_final: float | None
    m2_aniso_gate: float | None
    mean_ring_eccentricity: float
    defense_layer: str


StageCallback = Callable[[int, np.ndarray, np.ndarray, np.ndarray | None, dict], None]


def _sv_ratio(M2: np.ndarray) -> float:
    """SV_max / SV_min of 2x2 M2 (1.0 = isotropic).
    cv/approaches/fused/refine.py:96-104."""
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
    cv/approaches/fused/refine.py:107-131."""
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


def _make_residual_fn(
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
    """Build the residuals callable for ``least_squares``.

    Inlined from cv/approaches/fused/refine.py:138-227 with the off-by-one on
    the degenerate-det early return fixed (the iteredge version returned
    n_pts+9 there but n_pts+10 on success).
    """
    H, W = crop_shape
    n_pts = ring_pts_warped.shape[0]
    aff = np.asarray(affine_init, dtype=np.float64)
    H_aff = HomographyModel.params_to_H(aff)
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
        H_mat = HomographyModel.params_to_H(params)
        det_now = float(np.linalg.det(H_mat))
        if det_now <= 0.1 or det_now > 10.0:
            return np.full(out_len, 1e6, dtype=np.float64)
        try:
            H_inv = np.linalg.inv(H_mat)
        except np.linalg.LinAlgError:
            return np.full(out_len, 1e6, dtype=np.float64)

        crop_pts = HomographyModel.apply_H_to_points(H_inv, ring_pts_warped)
        x = crop_pts[:, 0]
        y = crop_pts[:, 1]
        in_bounds = (x >= 0) & (x < W) & (y >= 0) & (y < H)
        vals = HomographyModel.sample_potential(potential, crop_pts)

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


class FusedHomographyRefiner:
    """Coarse-to-fine 8-DOF homography refinement with layered orthogonality
    defenses.

    Defense layers (applied in order):
      1. SKIP if ``mean_ring_eccentricity < skip_ecc_threshold`` (1.02) →
         return multiring's affine H_init unchanged.
      2. LOCK AFFINE if ``ecc < affine_lock_ecc_threshold`` (1.10) →
         set ``lb[:6] = ub[:6] = aff_init[:6]``; optimizer only refines h31, h32.
      3. SCALE BOUNDS by eccentricity if ecc ≥ 1.10.
      4. SV-RATIO PENALTY in residual (soft).
      5. POST-REFINEMENT GATES: corner-radius-ratio + M2 anisotropy.

    Signature mirrors ``cv.approaches.fused.refine.refine_homography`` exactly
    so the math lifts verbatim. ``cal`` is the typed Calibration dataclass;
    consumers read ``cal.cx, cal.cy, cal.s_px``.

    Ported from cv/approaches/fused/refine.py:233-616.
    """

    @staticmethod
    def refine(
        gray_crop: np.ndarray,
        cal: Calibration,
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
    ) -> RefinementResult:
        cx, cy = cal.cx, cal.cy
        s_px_crop = cal.s_px  # crop-frame, for enhance_ring_edges
        ocx, ocy = warped_out_center

        # ----- Defense Layer 1: skip refinement entirely for orthogonal sources
        if mean_ring_eccentricity < skip_ecc_threshold:
            skip_reason = (f"skip_refinement: ecc={mean_ring_eccentricity:.3f} < "
                           f"threshold={skip_ecc_threshold}")
            if stage_callback is not None:
                stage_callback(
                    stage_idx=0,
                    current_params=np.asarray(affine_init_params, dtype=np.float64).copy(),
                    current_H=HomographyModel.params_to_H(affine_init_params),
                    potential=None,
                    info={"sigma": 0.0, "pot_kind": "skip", "cost": float("nan"),
                          "nfev": 0, "det": float(np.linalg.det(HomographyModel.params_to_H(affine_init_params))),
                          "reverted_to_init": True, "reason": skip_reason},
                )
            return RefinementResult(
                final_params=np.asarray(affine_init_params, dtype=np.float64),
                final_H=HomographyModel.params_to_H(affine_init_params),
                final_cost=float("nan"),
                n_iterations=0,
                converged=True,
                stages=[],
                reverted_to_init=True,
                revert_reason=skip_reason,
                init_data_score=float("nan"),
                opt_data_score=float("nan"),
                corner_ratio_init=None,
                corner_ratio_final=None,
                corner_ratio_gate=None,
                m2_aniso_init=None,
                m2_aniso_final=None,
                m2_aniso_gate=None,
                mean_ring_eccentricity=float(mean_ring_eccentricity),
                defense_layer="skip",
            )

        if s_px_crop <= 0 or s_warped <= 0 or r_bull_warped <= 0:
            return RefinementResult(
                final_params=np.asarray(affine_init_params, dtype=np.float64),
                final_H=HomographyModel.params_to_H(affine_init_params),
                final_cost=float("nan"),
                n_iterations=0,
                converged=False,
                stages=[],
                reverted_to_init=False,
                revert_reason="degenerate calibration",
                init_data_score=float("nan"),
                opt_data_score=float("nan"),
                corner_ratio_init=None,
                corner_ratio_final=None,
                corner_ratio_gate=None,
                m2_aniso_init=None,
                m2_aniso_final=None,
                m2_aniso_gate=None,
                mean_ring_eccentricity=float(mean_ring_eccentricity),
                defense_layer="degenerate",
            )

        if schedule is None:
            schedule = [(s_px_crop * f, rp, ra, rd, mi, pk, dw)
                        for (f, rp, ra, rd, mi, pk, dw) in DEFAULT_SCHEDULE]

        ring_pts = HomographyModel.ring_points_warped(
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
            eps = 1e-9
            lb = aff_init.copy() - eps
            ub = aff_init.copy() + eps
            lb[6] = -perspective_bound
            ub[6] = perspective_bound
            lb[7] = -perspective_bound
            ub[7] = perspective_bound
            defense_layer = "lock_affine"
        else:
            ecc_factor = max(1.0, (mean_ring_eccentricity - 1.0) * 10.0)
            bound_factor = affine_bound_base * ecc_factor
            scale = np.maximum(np.abs(aff_init), np.array([0.5] * 8))
            lb = aff_init - bound_factor * scale
            ub = aff_init + bound_factor * scale
            lb[6] = -perspective_bound
            ub[6] = perspective_bound
            lb[7] = -perspective_bound
            ub[7] = perspective_bound
            defense_layer = f"ecc_scaled(bf={bound_factor:.3f})"

        if stage_callback is not None:
            stage_callback(
                stage_idx=0,
                current_params=aff_init.copy(),
                current_H=HomographyModel.params_to_H(aff_init),
                potential=None,
                info={"sigma": 0.0, "pot_kind": "init", "cost": float("nan"),
                      "nfev": 0, "det": float(np.linalg.det(HomographyModel.params_to_H(aff_init)))},
            )

        for stage_idx, (sigma, reg_p, reg_a, reg_d, max_iters, pot_kind, dw) in enumerate(schedule, start=1):
            emap = EdgePotential.enhance_ring_edges(
                gray_crop, cx=cx, cy=cy, s_px=s_px_crop, smooth_sigma=sigma,
            )
            if edge_band_mask is not None and pot_kind == "dt":
                canny_filtered = (emap["canny"] > 0) & edge_band_mask
                canny_filtered_uint8 = (canny_filtered.astype(np.uint8)) * 255
                emap["dt"] = EdgePotential.edge_distance_transform(canny_filtered_uint8)
            potential = emap["mag_smooth"] if pot_kind == "mag" else emap["dt"]

            res_fn = _make_residual_fn(
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
            H_now = HomographyModel.params_to_H(result.x)
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
                                   HomographyModel.params_to_H(current_params), potential, stage_entry)
                continue

            current_params = result.x
            last_cost = cost
            converged = bool(result.success)
            stages_log.append(stage_entry)

            if stage_callback is not None:
                stage_callback(stage_idx, current_params.copy(),
                               H_now, potential, stage_entry)

        n_total_iters = sum(int(s.get("nfev", 0)) for s in stages_log)

        emap_final = EdgePotential.enhance_ring_edges(
            gray_crop, cx=cx, cy=cy, s_px=s_px_crop, smooth_sigma=s_px_crop * 0.08,
        )
        final_pot = emap_final["dt"]
        if edge_band_mask is not None:
            canny_filtered = (emap_final["canny"] > 0) & edge_band_mask
            canny_filtered_uint8 = (canny_filtered.astype(np.uint8)) * 255
            final_pot = EdgePotential.edge_distance_transform(canny_filtered_uint8)

        def data_residual(params: np.ndarray) -> float:
            H_mat = HomographyModel.params_to_H(params)
            det_now = float(np.linalg.det(H_mat))
            if det_now <= 0.1 or det_now > 10.0:
                return float("inf")
            H_inv = np.linalg.inv(H_mat)
            crop_pts = HomographyModel.apply_H_to_points(H_inv, ring_pts)
            x = crop_pts[:, 0]
            y = crop_pts[:, 1]
            in_bounds = (x >= 0) & (x < gray_crop.shape[1]) & (y >= 0) & (y < gray_crop.shape[0])
            vals = HomographyModel.sample_potential(final_pot, crop_pts)
            vals = np.where(in_bounds, np.minimum(vals, 30.0), 30.0)
            return float(np.mean(vals))

        init_score = data_residual(aff_init)
        final_score = data_residual(current_params)

        if corner_gate_enable:
            corner_ratio_init = _corner_radius_ratio(
                HomographyModel.params_to_H(aff_init), gray_crop.shape, cx, cy,
            )
            corner_ratio_final = _corner_radius_ratio(
                HomographyModel.params_to_H(current_params), gray_crop.shape, cx, cy,
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

        if enable_m2_aniso_gate:
            m2_aniso_init = _sv_ratio(HomographyModel.params_to_H(aff_init)[:2, :2])
            m2_aniso_final = _sv_ratio(HomographyModel.params_to_H(current_params)[:2, :2])
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
                "det": float(np.linalg.det(HomographyModel.params_to_H(aff_init))),
                "success": True, "pot": "revert",
                "reason": revert_reason,
            })
            if stage_callback is not None:
                stage_callback(
                    stage_idx=len(schedule) + 1,
                    current_params=aff_init.copy(),
                    current_H=HomographyModel.params_to_H(aff_init),
                    potential=final_pot,
                    info={"sigma": 0.0, "pot_kind": "revert", "cost": float(init_score),
                          "nfev": 0, "det": float(np.linalg.det(HomographyModel.params_to_H(aff_init))),
                          "reverted_to_init": True, "reason": revert_reason},
                )
            return RefinementResult(
                final_params=aff_init,
                final_H=HomographyModel.params_to_H(aff_init),
                final_cost=float(init_score),
                n_iterations=n_total_iters,
                converged=True,
                stages=stages_log,
                reverted_to_init=True,
                revert_reason=revert_reason,
                init_data_score=init_score,
                opt_data_score=final_score,
                corner_ratio_init=corner_ratio_init,
                corner_ratio_final=corner_ratio_final,
                corner_ratio_gate=corner_gate_threshold,
                m2_aniso_init=m2_aniso_init,
                m2_aniso_final=m2_aniso_final,
                m2_aniso_gate=m2_aniso_gate_threshold if enable_m2_aniso_gate else None,
                mean_ring_eccentricity=float(mean_ring_eccentricity),
                defense_layer=defense_layer,
            )

        return RefinementResult(
            final_params=current_params,
            final_H=HomographyModel.params_to_H(current_params),
            final_cost=last_cost,
            n_iterations=n_total_iters,
            converged=converged,
            stages=stages_log,
            reverted_to_init=False,
            revert_reason=None,
            init_data_score=init_score,
            opt_data_score=final_score,
            corner_ratio_init=corner_ratio_init,
            corner_ratio_final=corner_ratio_final,
            corner_ratio_gate=corner_gate_threshold,
            m2_aniso_init=m2_aniso_init,
            m2_aniso_final=m2_aniso_final,
            m2_aniso_gate=m2_aniso_gate_threshold if enable_m2_aniso_gate else None,
            mean_ring_eccentricity=float(mean_ring_eccentricity),
            defense_layer=defense_layer,
        )
