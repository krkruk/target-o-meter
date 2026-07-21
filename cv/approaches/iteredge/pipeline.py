"""End-to-end iteredge pipeline: intake → localize → edges → optimize → warp →
normalize → detect → invert → visualize.

Per image, produces 9 PNGs + 1 JSON under out_dir (see module docstring in
run.py for the file manifest).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from cv.approaches.iteredge.edges import canny_edges, enhance_ring_edges
from cv.approaches.iteredge.localize import crop_to_target as iteredge_crop
from cv.approaches.iteredge.model import (
    H_to_params,
    apply_H_to_points,
    params_to_H,
    ring_points_warped,
)
from cv.approaches.iteredge.normalize import (
    IterEdgeTransformMeta,
    crop_to_source_xy,
    norm_to_crop,
    norm_to_source,
    norm_to_warped,
    normalize_to_1024,
    self_test_inversion,
    warped_to_crop,
)
from cv.approaches.iteredge.optimize import optimize_homography
from cv.approaches.iteredge.warp import apply_warp, compute_output_shape
from cv.blob_detect import (
    calibrate as bd_calibrate,
    score_holes,
    to_gray,
    warp_fronto_parallel,
)
from cv.detector_base import DetectionResult, HoleDetector, TargetType
from cv.gt import load_bgr


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------
def _draw_canny_in_color(canny_bin: np.ndarray) -> np.ndarray:
    """Canny edges (255 where edge) → red on black."""
    h, w = canny_bin.shape
    out = np.zeros((h, w, 3), dtype=np.uint8)
    mask = canny_bin > 0
    out[mask] = (0, 0, 255)  # BGR red
    return out


def _overlay_edges_on_crop(crop_gray: np.ndarray, canny_bin: np.ndarray) -> np.ndarray:
    """Crop in grayscale, Canny edges in red on top (50% blend)."""
    bgr = cv2.cvtColor(crop_gray, cv2.COLOR_GRAY2BGR)
    edges = _draw_canny_in_color(canny_bin)
    # Blend edges onto bgr only where edges exist.
    mask = (canny_bin > 0)
    bgr[mask] = (0, 0, 255)
    return bgr


def _draw_rings_on_image(
    base_bgr: np.ndarray,
    H_inv: np.ndarray,
    ocx: float, ocy: float,
    r_bull: float, s: float,
    color: tuple[int, int, int],
    thickness: int = 1,
    dashed: bool = False,
    n_rings: int = 10,
    n_pts: int = 96,
) -> np.ndarray:
    """Draw rings (predicted in warped frame, mapped back to crop frame) on
    a BGR copy of base."""
    out = base_bgr.copy()
    pts_warped = ring_points_warped(ocx, ocy, r_bull, s, n_rings=n_rings, n_per_ring=n_pts)
    pts_crop = apply_H_to_points(H_inv, pts_warped)
    # Per ring, draw a polyline.
    for k in range(n_rings):
        chunk = pts_crop[k * n_pts:(k + 1) * n_pts]
        if len(chunk) < 2:
            continue
        if dashed:
            for i in range(0, len(chunk) - 1, 2):
                cv2.line(out,
                         (int(chunk[i, 0]), int(chunk[i, 1])),
                         (int(chunk[i + 1, 0]), int(chunk[i + 1, 1])),
                         color, thickness)
        else:
            cv2.polylines(out, [chunk.astype(np.int32)], True, color, thickness)
    return out


def _draw_magenta_on_gray(gray: np.ndarray, points: list[tuple[float, float]],
                          radius: int = 10, with_score: Optional[list[int]] = None) -> np.ndarray:
    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for i, (x, y) in enumerate(points):
        cv2.circle(bgr, (int(x), int(y)), radius, (255, 0, 255), -1)
        if with_score is not None and i < len(with_score):
            cv2.putText(bgr, str(with_score[i]), (int(x) + radius + 2, int(y) + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 255), 2)
    return bgr


def _draw_magenta_on_bgr(bgr: np.ndarray, points: list[tuple[float, float]],
                         radius: int = 16, with_score: Optional[list[int]] = None) -> np.ndarray:
    out = bgr.copy()
    for i, (x, y) in enumerate(points):
        cv2.circle(out, (int(x), int(y)), radius, (255, 0, 255), -1)
        if with_score is not None and i < len(with_score):
            cv2.putText(out, str(with_score[i]), (int(x) + radius + 2, int(y) + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 255), 2)
    return out


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def run_pipeline(
    image_path: Path,
    detector: HoleDetector,
    target_type: TargetType = "air_pistol",
    caliber_hint: Optional[str] = None,
    out_dir: Optional[Path] = None,
    write_intermediates: bool = True,
) -> dict:
    """Run the full iteredge pipeline on one image."""
    bgr = load_bgr(image_path)
    gray = to_gray(bgr)
    stem = image_path.stem
    out_path = Path(out_dir) if out_dir else None
    if write_intermediates and out_path:
        out_path.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_path / f"{stem}_01_intake.png"), bgr)

    # ----- Stage 1: localize + initial crop -----
    crop, bbox, init = iteredge_crop(gray)

    if write_intermediates and out_path:
        cv2.imwrite(str(out_path / f"{stem}_02_crop.png"),
                    cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR))

    # ----- Stage 2: use the localization's calibration (already validated) -----
    # We trust the init values that the localizer produced. Build a `cal` dict
    # that matches bd_calibrate's shape.
    s_init = float(init.get("s_px_init", 0) or 0)
    r_bw_init = float(init.get("r_bw_px_init", 0) or 0)
    r_bull_init = r_bw_init - 3.0 * s_init if (s_init > 0 and r_bw_init > 0) else 0.0

    if s_init > 0 and r_bw_init > 0 and r_bull_init > 0:
        cx_cal = float(init["cx_crop"])
        cy_cal = float(init["cy_crop"])
        # Use init's anisotropy/major_dir/semi if provided; else isotropic.
        semi_a = float(init.get("semi_a_init", r_bw_init))
        semi_b = float(init.get("semi_b_init", r_bw_init))
        major_dir = init.get("major_dir_init", np.array([1.0, 0.0]))
        aniso = float(init.get("anisotropy_init", 1.0))
        # Sanity: if the black-disc center wandered away from the trusted
        # bullseye estimate, recompute (this only happens when the recrop
        # recalibrated).
        try:
            from cv.blob_detect import blackdisc_center
            cx_bd, cy_bd, aniso_bd, major_dir_bd, semi_a_bd, semi_b_bd = blackdisc_center(crop)
            if math.hypot(cx_bd - cx_cal, cy_bd - cy_cal) < 1.5 * s_init:
                # Use the locally-refined values if they're sane.
                cx_cal, cy_cal = float(cx_bd), float(cy_bd)
                if 0.5 * s_init < semi_a_bd < 2.0 * r_bw_init:
                    semi_a = float(semi_a_bd)
                    semi_b = float(semi_b_bd)
                    major_dir = major_dir_bd
                    aniso = float(aniso_bd)
        except Exception:
            pass
        cal = {
            "ok": True,
            "shape": crop.shape,
            "cx": cx_cal, "cy": cy_cal,
            "r_bw_px": r_bw_init,
            "r_bull_px": float(r_bull_init),
            "s_px": s_init,
            "anisotropy": aniso,
            "major_dir": major_dir,
            "semi_a": semi_a,
            "semi_b": semi_b,
            "peaks_aligned": int(init.get("score", 0)),
        }
    else:
        # Last-resort: re-calibrate from scratch.
        cal = bd_calibrate(crop)
        if not cal.get("ok"):
            cal = {
                "ok": True, "shape": crop.shape,
                "cx": float(init["cx_crop"]), "cy": float(init["cy_crop"]),
                "r_bw_px": 100.0, "r_bull_px": 30.0, "s_px": 30.0,
                "anisotropy": 1.0, "major_dir": np.array([1.0, 0.0]),
                "semi_a": 100.0, "semi_b": 100.0, "peaks_aligned": 0,
            }

    # ----- Stage 3: affine warp init (warm start for the optimizer) -----
    warped_affine, M2, out_center_affine = warp_fronto_parallel(crop, cal)
    ocx_init, ocy_init = float(out_center_affine[0]), float(out_center_affine[1])
    cx_cal, cy_cal = float(cal["cx"]), float(cal["cy"])
    t_affine = np.array([ocx_init, ocy_init]) - M2 @ np.array([cx_cal, cy_cal])

    from cv.approaches.iteredge.model import affine_init_params
    aff_init = affine_init_params(M2, t_affine)

    # ----- Stage 4: edge detection (for diagnostics + optimizer) -----
    canny = canny_edges(crop)

    # ----- Stage 5: optimize homography -----
    opt = optimize_homography(
        gray_crop=crop,
        cal=cal,
        affine_init_params=aff_init,
        affine_M2=M2,
        affine_t=t_affine,
        warped_out_center=(ocx_init, ocy_init),
    )
    H_opt = opt["final_H"]
    H_params = opt["final_params"]

    # ----- Stage 6: warp the crop with the optimized H -----
    # The optimizer's H maps crop → warped with the bullseye landing near
    # (ocx_init, ocy_init). We compose with a small translation to recenter.
    r_ring1_warped_init = float(cal["r_bull_px"] + 9.0 * cal["s_px"])
    out_w, out_h, H_full = compute_output_shape(
        H_opt, crop.shape, cx_cal, cy_cal, r_ring1_warped_init, margin_factor=1.30,
    )
    warped = apply_warp(crop, H_full, (out_w, out_h))

    # The bullseye in warped frame (under H_full) is now (out_w/2, out_h/2)
    # by construction of compute_output_shape.
    bull_warped = (out_w / 2.0, out_h / 2.0)
    r_ring1_warped = r_ring1_warped_init  # Optimizer preserved the warped scale.

    # ----- Stage 7: normalize to 1024x1024 -----
    image_1024, meta = normalize_to_1024(
        warped=warped,
        H_full=H_full,
        bullseye_warped=bull_warped,
        bbox=bbox,
        r_ring1_warped=r_ring1_warped,
        cx_crop=cx_cal, cy_crop=cy_cal,
    )
    invert_err = self_test_inversion(meta)

    # ----- Stage 8: detect (mock) -----
    result = detector.detect(image_1024, target_type=target_type, caliber_hint=caliber_hint)

    holes_crop: list[tuple[float, float]] = []
    holes_src: list[tuple[float, float]] = []
    for h in result.holes:
        xy_crop = norm_to_crop(float(h.x), float(h.y), meta)
        xy_src = crop_to_source_xy(*xy_crop, meta)
        holes_crop.append(xy_crop)
        holes_src.append(xy_src)

    # Classical scoring uses the crop-frame positions + the affine-calibrated
    # bullseye / ring spacing. Synthetic hole radius.
    synthetic_r = max(3.0, 0.15 * float(cal["s_px"]))
    holes_crop_with_r = [(xy[0], xy[1], synthetic_r) for xy in holes_crop]
    classical_scores = score_holes(holes_crop_with_r, cal)

    # ----- Build result dict -----
    result_dict = {
        "image": image_path.name,
        "ok": True,
        "approach": "iteredge",
        "detector": result.detector_name,
        "target_type": result.target_type,
        "caliber_hint": caliber_hint,
        "crop_bbox": [int(v) for v in bbox],
        "initial_guess": {
            "type": "affine_from_blob_detect",
            "bullseye_init": [float(cx_cal), float(cy_cal)],
            "s_px_init": float(cal["s_px"]),
            "r_bull_px_init": float(cal["r_bull_px"]),
        },
        "optimization": {
            "parameterization": "homography_8dof",
            "final_cost": float(opt["final_cost"]),
            "n_iterations": int(opt["n_iterations"]),
            "converged": bool(opt["converged"]),
            "final_params": [float(p) for p in H_params],
            "n_stages": len(opt["stages"]),
            "stages": [
                {k: (float(v) if isinstance(v, (int, float)) else v) for k, v in s.items()}
                for s in opt["stages"]
            ],
            "reverted_to_init": bool(opt.get("reverted_to_init", False)),
            "init_data_score": float(opt.get("init_data_score", float("nan"))),
            "opt_data_score": float(opt.get("opt_data_score", float("nan"))),
        },
        "calibration": {
            "ok": bool(cal["ok"]),
            "cx": float(cal["cx"]), "cy": float(cal["cy"]),
            "r_bw_px": float(cal["r_bw_px"]),
            "r_bull_px": float(cal["r_bull_px"]),
            "s_px": float(cal["s_px"]),
            "anisotropy": float(cal["anisotropy"]),
            "peaks_aligned": int(cal.get("peaks_aligned", 0)),
        },
        "calibration_after": {
            "cx": float(cal["cx"]), "cy": float(cal["cy"]),
            "s_px": float(cal["s_px"]),
            "r_bull_px": float(cal["r_bull_px"]),
        },
        "norm_meta": {
            "scale": float(meta.scale),
            "tx": float(meta.tx), "ty": float(meta.ty),
            "size": int(meta.size),
            "target_ring1_px": 500.0,
            "r_ring1_warped": float(meta.r_ring1_warped),
        },
        "self_test": {
            "bullseye_invert_err_px": float(invert_err),
            "passed": bool(invert_err < 0.01),
        },
        "holes_norm": [h.to_dict() for h in result.holes],
        "holes_crop": [{"x": float(xy[0]), "y": float(xy[1])} for xy in holes_crop],
        "holes_src": [{"x": float(xy[0]), "y": float(xy[1])} for xy in holes_src],
        "scores_llm": [int(h.score) for h in result.holes],
        "scores_classical": [int(s) for s in classical_scores],
        "count": len(result.holes),
        "total_llm": int(sum(h.score for h in result.holes)),
        "total_classical": int(sum(classical_scores)),
        "notes": result.notes,
    }

    # ----- Write intermediates -----
    if write_intermediates and out_path:
        # _02b_detect.png: crop + Canny edges (red) + initial rings (yellow dashed) + final rings (green solid).
        crop_bgr = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        crop_with_edges = crop_bgr.copy()
        crop_with_edges[canny > 0] = (0, 0, 255)

        # Initial rings: identity H = the affine init.
        H_init_inv = np.linalg.inv(params_to_H(aff_init))
        init_rings = _draw_rings_on_image(
            crop_with_edges, H_init_inv,
            ocx_init, ocy_init,
            float(cal["r_bull_px"]), float(cal["s_px"]),
            color=(0, 255, 255), thickness=1, dashed=True,
        )

        # Final rings: optimized H.
        H_opt_inv = np.linalg.inv(H_full)
        final_rings = _draw_rings_on_image(
            init_rings, H_opt_inv,
            out_w / 2.0, out_h / 2.0,
            float(cal["r_bull_px"]), float(cal["s_px"]),
            color=(0, 255, 0), thickness=2, dashed=False,
        )
        cv2.imwrite(str(out_path / f"{stem}_02b_detect.png"), final_rings)

        # _03_warp.png
        cv2.imwrite(str(out_path / f"{stem}_03_warp.png"),
                    cv2.cvtColor(warped, cv2.COLOR_GRAY2BGR))

        # _04_llm_input.png
        cv2.imwrite(str(out_path / f"{stem}_04_llm_input.png"),
                    cv2.cvtColor(image_1024, cv2.COLOR_GRAY2BGR))

        # _05_llm_predict.png
        llm_scores = [h.score for h in result.holes]
        cv2.imwrite(str(out_path / f"{stem}_05_llm_predict.png"),
                    _draw_magenta_on_gray(image_1024, [(h.x, h.y) for h in result.holes], with_score=llm_scores))

        # _06_crop_predict.png
        crop_viz = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        # Draw ring overlay (extrapolated, using final H^-1).
        crop_viz = _draw_rings_on_image(
            crop_viz, H_opt_inv,
            out_w / 2.0, out_h / 2.0,
            float(cal["r_bull_px"]), float(cal["s_px"]),
            color=(60, 200, 60), thickness=1, dashed=False,
        )
        for (x, y), sc in zip(holes_crop, classical_scores):
            cv2.circle(crop_viz, (int(x), int(y)), max(3, int(synthetic_r)), (255, 0, 255), -1)
            cv2.putText(crop_viz, str(sc), (int(x) + int(synthetic_r) + 2, int(y) + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 255), 2)
        cv2.imwrite(str(out_path / f"{stem}_06_crop_predict.png"), crop_viz)

        # _07_source_predict.png
        cv2.imwrite(str(out_path / f"{stem}_07_source_predict.png"),
                    _draw_magenta_on_bgr(bgr, holes_src, with_score=llm_scores))

        (out_path / f"{stem}_result.json").write_text(json.dumps(result_dict, indent=2))

    return result_dict
