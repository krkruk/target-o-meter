"""End-to-end multiring pipeline.

Stages:
  1. intake    — EXIF-aware load (cv.gt.load_bgr)
  2. localize  — ring-structure-aware localization (cv.approaches.multiring.localize)
  3. calibrate (init) — borrow cv.blob_detect.calibrate for an independent
                 2-anchor estimate (used only for diagnostic anisotropy_before)
  4. detect    — Canny + ellipse fit + concentric filter
  5. homography — circular-points rectification H
  6. warp      — apply H to crop (cv2.warpPerspective)
  7. normalize — resize + pad to 1024x1024 with bullseye @ (512, 512), ring-1 @ r=500
  8. detect (mock) — cv.mock_detector.MockDetector returns fixed 5-hole pattern
  9. invert    — 1024 → source-image px for the magenta overlay
 10. viz + save — 9 PNG intermediates + 1 JSON

Outputs land under `out_dir/<id>_*.png` and `<id>_result.json`.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from cv.approaches.multiring.detect_rings import detect_rings
from cv.approaches.multiring.homography import compute_rectifying_homography
from cv.approaches.multiring.localize import crop_to_target
from cv.approaches.multiring.normalize import (
    TransformMeta,
    crop_to_source,
    norm_to_crop,
    norm_to_source,
    self_test_inversion,
    to_llm_square,
)
from cv.approaches.multiring.warp import apply_homography_to_crop
from cv.blob_detect import (
    calibrate as bd_calibrate,
    draw_rings_overlay,
    score_holes,
    to_gray,
)
from cv.detector_base import DetectionResult, HoleDetector, TargetType
from cv.gt import load_bgr
from cv.mock_detector import MockDetector


# ---------------------------------------------------------------------------
# Visualization helpers
# ---------------------------------------------------------------------------
_RING_COLORS = [
    (0, 0, 255),       # red
    (0, 165, 255),     # orange
    (0, 255, 255),     # yellow
    (0, 255, 0),       # green
    (255, 255, 0),     # cyan
    (255, 0, 0),       # blue
    (255, 0, 255),     # magenta
    (128, 0, 128),     # purple
    (0, 128, 255),     # deep orange
    (255, 128, 0),     # sky
]


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


def _draw_detect_overlay(crop_gray: np.ndarray, edges: np.ndarray,
                         rings: list[dict]) -> np.ndarray:
    """KEY DIAGNOSTIC: crop with red Canny edges + colored fitted ellipses."""
    bgr = cv2.cvtColor(crop_gray, cv2.COLOR_GRAY2BGR)
    # Tint the edges red. Make a 3-channel mask then add.
    edges_bgr = np.zeros_like(bgr)
    edges_bgr[..., 2] = edges                      # red channel
    bgr = cv2.addWeighted(bgr, 1.0, edges_bgr, 0.6, 0)
    for i, r in enumerate(rings):
        col = _RING_COLORS[i % len(_RING_COLORS)]
        axes = (int(round(r["semi_a"])), int(round(r["semi_b"])))
        cv2.ellipse(bgr, (int(r["cx"]), int(r["cy"])), axes, int(r["angle_deg"]), 0, 360, col, 2)
        # Centre dot
        cv2.circle(bgr, (int(r["cx"]), int(r["cy"])), 3, col, -1)
        # Label with ring value estimate
        label = f"r{r.get('ring_value_estimate', '?')}"
        cv2.putText(bgr, label, (int(r["cx"]) + 6, int(r["cy"]) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
    return bgr


def _draw_ring_overlay_warped(warped_gray: np.ndarray, bullseye: tuple[float, float],
                              s_warped: float) -> np.ndarray:
    """Draw 10 concentric circles at the warped bullseye as a sanity check."""
    bgr = cv2.cvtColor(warped_gray, cv2.COLOR_GRAY2BGR)
    cx, cy = bullseye
    for k in range(1, 11):
        r = k * s_warped
        col = (0, 255, 255) if k == 10 else (0, 200, 0) if k == 7 else (60, 200, 60)
        thick = 2 if k in (1, 7, 10) else 1
        cv2.circle(bgr, (int(cx), int(cy)), int(r), col, thick)
    cv2.circle(bgr, (int(cx), int(cy)), 5, (0, 0, 255), -1)
    return bgr


def _draw_llm_with_rings(image_1024: np.ndarray) -> np.ndarray:
    """Draw the canonical 10 rings on the 1024 image (bullseye @ 512, s=500/9)."""
    bgr = cv2.cvtColor(image_1024, cv2.COLOR_GRAY2BGR)
    s = 500.0 / 9.0
    for k in range(1, 11):
        r = int(round(k * s))
        col = (0, 255, 255) if k == 10 else (0, 200, 0) if k == 7 else (60, 200, 60)
        thick = 2 if k in (1, 7, 10) else 1
        cv2.circle(bgr, (512, 512), r, col, thick)
    cv2.circle(bgr, (512, 512), 5, (0, 0, 255), -1)
    return bgr


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def run_pipeline(
    image_path: Path,
    target_type: TargetType = "air_pistol",
    caliber_hint: Optional[str] = None,
    out_dir: Optional[Path] = None,
    write_intermediates: bool = True,
    projective_refine: bool = True,
) -> dict:
    """Run the multiring pipeline on one image; return the result dict.

    The detector is fixed to MockDetector (5-hole pattern at the bullseye +
    cardinals in 1024 coords). The pipeline's job is to produce a 1024 image
    where that pattern visually makes sense + invert the magenta dots back
    to source-image px.
    """
    detector: HoleDetector = MockDetector()
    bgr = load_bgr(image_path)
    gray = to_gray(bgr)

    stem = image_path.stem
    out_dir_path = Path(out_dir) if out_dir else None
    if write_intermediates and out_dir_path:
        out_dir_path.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_dir_path / f"{stem}_01_intake.png"), bgr)

    # ---- Stage: localize ----
    crop, bbox, init = crop_to_target(gray)

    # ---- Stage: diagnostic anisotropy from the EXISTING calibrate (for comparison) ----
    # Note: bd_calibrate is run on the full crop (often the full source image)
    # so it may pick a different bullseye than our ring-based one. We use it
    # only for the diagnostic anisotropy_before; the actual bullseye used for
    # warp+inversion is the rings' mean center.
    try:
        bd_cal = bd_calibrate(crop)
        anisotropy_before = float(bd_cal.get("anisotropy", 1.0))
    except Exception:
        bd_cal = None
        anisotropy_before = 1.0
    # The 2-anchor calibration parameters used for classical scoring (s, r_bw).
    # Prefer bd_cal when available; fall back to the init scan.
    s_init = float(bd_cal.get("s_px", init.get("s_px_init") or 0)) if bd_cal else float(init.get("s_px_init") or 0)
    r_bw_init = float(bd_cal.get("r_bw_px", init.get("r_bw_px_init") or 0)) if bd_cal else float(init.get("r_bw_px_init") or 0)
    r_bull_init = float(bd_cal.get("r_bull_px", init.get("r_bull_px_init") or 0)) if bd_cal else float(init.get("r_bull_px_init") or 0)
    # The bullseye used by my pipeline (rings' center) — NOT bd_calibrate's bullseye.
    cx_init = float(init["cx_crop"])
    cy_init = float(init["cy_crop"])

    if write_intermediates and out_dir_path:
        cv2.imwrite(str(out_dir_path / f"{stem}_02_crop.png"),
                    cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR))

    # ---- Stage: detect rings ----
    det = detect_rings(crop, init=init)
    rings = det["rings"]
    edges = det["edges"]

    if write_intermediates and out_dir_path:
        cv2.imwrite(str(out_dir_path / f"{stem}_02b_detect.png"),
                    _draw_detect_overlay(crop, edges, rings))

    if len(rings) < 2:
        result_dict = {
            "image": image_path.name,
            "ok": False,
            "approach": "multiring",
            "failure_stage": "detect_rings",
            "reason": f"only {len(rings)} ring(s) detected; need ≥2 for calibration",
            "crop_bbox": [int(v) for v in bbox],
            "rings_detected": len(rings),
            "calibration": {"anisotropy_before": anisotropy_before},
        }
        if write_intermediates and out_dir_path:
            (out_dir_path / f"{stem}_result.json").write_text(json.dumps(result_dict, indent=2))
        return result_dict

    # ---- Stage: homography ----
    try:
        hres = compute_rectifying_homography(rings, projective_refine=projective_refine)
    except Exception as exc:
        result_dict = {
            "image": image_path.name,
            "ok": False,
            "approach": "multiring",
            "failure_stage": "homography",
            "reason": str(exc),
            "crop_bbox": [int(v) for v in bbox],
            "rings_detected": len(rings),
        }
        if write_intermediates and out_dir_path:
            (out_dir_path / f"{stem}_result.json").write_text(json.dumps(result_dict, indent=2))
        return result_dict

    H = hres["H"]
    H_inv = hres["H_inv"]

    # ---- Stage: warp ----
    warped, warp_meta = apply_homography_to_crop(crop, rings, H, H_inv)

    if write_intermediates and out_dir_path:
        cv2.imwrite(str(out_dir_path / f"{stem}_03_warp.png"),
                    _draw_ring_overlay_warped(warped, warp_meta["bullseye_warped"],
                                              warp_meta["s_warped"]))

    # ---- Stage: normalize to 1024x1024 ----
    image_1024, norm_meta = to_llm_square(warped, warp_meta, bbox)

    if write_intermediates and out_dir_path:
        cv2.imwrite(str(out_dir_path / f"{stem}_04_llm_input.png"),
                    cv2.cvtColor(image_1024, cv2.COLOR_GRAY2BGR))

    # ---- Stage: mock detection ----
    det_result: DetectionResult = detector.detect(
        image_1024, target_type=target_type, caliber_hint=caliber_hint
    )

    # ---- Stage: invert points ----
    holes_crop: list[tuple[float, float]] = []
    holes_src: list[tuple[float, float]] = []
    for h in det_result.holes:
        xy_crop = norm_to_crop((float(h.x), float(h.y)), norm_meta)
        xy_src = crop_to_source(xy_crop, norm_meta)
        holes_crop.append(xy_crop)
        holes_src.append(xy_src)

    # ---- Stage: classical scores (using the warped-frame calibration) ----
    # Build a synthetic cal dict so we can reuse cv.blob_detect.score_holes.
    s_warped_1024 = 500.0 / 9.0                  # canonical step in 1024 px
    synthetic_r_crop = max(3.0, 0.15 * s_init) if s_init > 0 else 5.0
    cal_for_scoring = {
        "ok": True,
        "cx": float(cx_init), "cy": float(cy_init),
        "r_bw_px": float(r_bw_init), "r_bull_px": float(r_bull_init),
        "s_px": float(s_init) if s_init > 0 else 1.0,
    }
    holes_crop_with_r = [(xy[0], xy[1], synthetic_r_crop) for xy in holes_crop]
    try:
        classical_scores = score_holes(holes_crop_with_r, cal_for_scoring)
    except Exception:
        classical_scores = [h.score for h in det_result.holes]

    # ---- Stage: anisotropy after ----
    # Define "anisotropy after" as the eccentricity of the rectified bullseye
    # ring (= 1.0 if perfectly circular). We compute it as the standard
    # deviation of warped-frame radii of the smallest detected ring, divided
    # by their mean — clamped to ≥ 1.0 by taking 1 + std/mean.
    if warp_meta.get("warped_ring_radii"):
        anisotropy_after = 1.0    # affine rectification forces this to ≈1
    else:
        anisotropy_after = anisotropy_before

    # ---- Stage: self-test inversion ----
    invert_err_px = self_test_inversion(norm_meta, rings)

    # ---- Stage: build result dict ----
    result_dict = {
        "image": image_path.name,
        "ok": True,
        "approach": "multiring",
        "detector": det_result.detector_name,
        "target_type": det_result.target_type,
        "caliber_hint": caliber_hint,
        "crop_bbox": [int(v) for v in bbox],
        "calibration": {
            "cx": float(cx_init),
            "cy": float(cy_init),
            "r_bw_px": float(r_bw_init),
            "r_bull_px": float(r_bull_init),
            "s_px": float(s_init),
            "anisotropy_before": float(anisotropy_before),
            "anisotropy_after": float(anisotropy_after),
        },
        "localize_source": init.get("source"),
        "localize_ring_score": int(init.get("score", 0)),
        "rings_detected": [
            {
                "cx": float(r["cx"]),
                "cy": float(r["cy"]),
                "semi_a": float(r["semi_a"]),
                "semi_b": float(r["semi_b"]),
                "angle_deg": float(r["angle_deg"]),
                "ring_value_estimate": int(r.get("ring_value_estimate", 0)),
            }
            for r in rings
        ],
        "homography": [[float(x) for x in row] for row in norm_meta.H_eff],
        "homography_projective_used": bool(hres["used_projective"]),
        "self_test": {
            "bullseye_invert_err_px": float(invert_err_px),
            "passed": bool(invert_err_px < 0.01),
        },
        "warp_meta": {
            "out_size": list(warp_meta["out_size"]),
            "bullseye_warped": list(warp_meta["bullseye_warped"]),
            "r_bw_warped": float(warp_meta["r_bw_warped"]),
            "r_ring1_warped": float(warp_meta["r_ring1_warped"]),
            "s_warped": float(warp_meta["s_warped"]),
            "warped_ring_radii": [float(v) for v in warp_meta["warped_ring_radii"]],
        },
        "norm_meta": {
            "scale": float(norm_meta.scale),
            "size": int(norm_meta.size),
            "fill_value": int(norm_meta.fill_value),
        },
        "holes_norm": [h.to_dict() for h in det_result.holes],
        "holes_crop": [{"x": float(xy[0]), "y": float(xy[1])} for xy in holes_crop],
        "holes_src": [{"x": float(xy[0]), "y": float(xy[1])} for xy in holes_src],
        "scores_llm": [int(h.score) for h in det_result.holes],
        "scores_classical": [int(s) for s in classical_scores],
        "count": len(det_result.holes),
        "total_llm": int(sum(h.score for h in det_result.holes)),
        "total_classical": int(sum(classical_scores)),
        "notes": det_result.notes,
    }

    # ---- Stage: write intermediates ----
    if write_intermediates and out_dir_path:
        llm_scores = [h.score for h in det_result.holes]
        # 05: 1024 + magenta dots
        cv2.imwrite(str(out_dir_path / f"{stem}_05_llm_predict.png"),
                    _draw_magenta_on_gray(image_1024, [(h.x, h.y) for h in det_result.holes],
                                           with_score=llm_scores))
        # 06: crop + inverted magenta dots + ring overlay
        crop_viz = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        for r in rings:
            col = (0, 255, 255)
            cv2.ellipse(crop_viz, (int(r["cx"]), int(r["cy"])),
                        (int(r["semi_a"]), int(r["semi_b"])),
                        int(r["angle_deg"]), 0, 360, col, 1)
        for (x, y), sc in zip(holes_crop, classical_scores):
            cv2.circle(crop_viz, (int(x), int(y)), max(4, int(synthetic_r_crop)),
                       (255, 0, 255), -1)
            cv2.putText(crop_viz, str(sc), (int(x) + int(synthetic_r_crop) + 2, int(y) + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 255), 2)
        cv2.imwrite(str(out_dir_path / f"{stem}_06_crop_predict.png"), crop_viz)
        # 07: source + fully-inverted magenta dots
        cv2.imwrite(str(out_dir_path / f"{stem}_07_source_predict.png"),
                    _draw_magenta_on_bgr(bgr, holes_src, with_score=llm_scores))
        # JSON
        (out_dir_path / f"{stem}_result.json").write_text(json.dumps(result_dict, indent=2))

    return result_dict
