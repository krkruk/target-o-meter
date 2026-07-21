"""End-to-end pipeline: intake -> localize -> calibrate -> warp -> normalize -> detect -> invert -> viz.

Deterministic orchestration that wraps any HoleDetector strategy. The detector
is the only variable piece; everything else is geometry.

Per image, produces 7 PNGs + 1 JSON under out_dir:
  <id>_01_intake.png         EXIF-oriented source image
  <id>_02_crop.png           after crop_to_target (localization)
  <id>_03_warp.png           after warp_fronto_parallel (orthogonalization)
  <id>_04_llm_input.png      the 1024x1024 normalized image (LLM input)
  <id>_05_llm_predict.png    1024 image + magenta dots at predicted positions
  <id>_06_crop_predict.png   original crop + magenta dots inverted to crop coords
  <id>_07_source_predict.png full source image + magenta dots fully inverted
  <id>_result.json           structured output (mirrors existing schema + extensions)

Result JSON has both LLM scores and classical scores (computed from the same
inverted positions) so the two scoring paths can be compared.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from cv.blob_detect import (
    calibrate, crop_to_target, deliverable, draw_rings_overlay, score_holes, to_gray,
)
from cv.detector_base import DetectionResult, DetectedHole, HoleDetector, TargetType
from cv.gt import load_bgr
from cv.normalize import (
    TransformMeta, crop_to_source, norm_to_crop, norm_to_source, self_test_inversion,
    to_llm_square, wrap_warp,
)


def _draw_magenta_on_gray(gray: np.ndarray, points: list[tuple[float, float]],
                          radius: int = 10, with_score: Optional[list[int]] = None) -> np.ndarray:
    """Draw filled magenta circles (and optional score numbers) on a grayscale image."""
    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for i, (x, y) in enumerate(points):
        cv2.circle(bgr, (int(x), int(y)), radius, (255, 0, 255), -1)
        if with_score is not None and i < len(with_score):
            cv2.putText(bgr, str(with_score[i]), (int(x) + radius + 2, int(y) + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 255), 2)
    return bgr


def _draw_magenta_on_bgr(bgr: np.ndarray, points: list[tuple[float, float]],
                         radius: int = 16, with_score: Optional[list[int]] = None) -> np.ndarray:
    """Draw filled magenta circles (and optional score numbers) on a BGR image."""
    out = bgr.copy()
    for i, (x, y) in enumerate(points):
        cv2.circle(out, (int(x), int(y)), radius, (255, 0, 255), -1)
        if with_score is not None and i < len(with_score):
            cv2.putText(out, str(with_score[i]), (int(x) + radius + 2, int(y) + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 255), 2)
    return out


def run_pipeline(
    image_path: Path,
    detector: HoleDetector,
    target_type: TargetType = "air_pistol",
    caliber_hint: Optional[str] = None,
    out_dir: Optional[Path] = None,
    write_intermediates: bool = True,
) -> dict:
    """Run the full pipeline on one image; return the result dict.

    If calibrate fails, returns early with ok=False and writes only intake+crop.
    """
    bgr = load_bgr(image_path)
    gray = to_gray(bgr)
    crop, bbox = crop_to_target(gray)
    cal = calibrate(crop)

    stem = image_path.stem
    out_dir_path = Path(out_dir) if out_dir else None
    if write_intermediates and out_dir_path:
        out_dir_path.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_dir_path / f"{stem}_01_intake.png"), bgr)
        cv2.imwrite(str(out_dir_path / f"{stem}_02_crop.png"), cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR))

    if not cal.get("ok"):
        result_dict = {
            "image": image_path.name,
            "ok": False,
            "failure_stage": "calibrate",
            "calibration": {k: (float(v) if isinstance(v, (int, float, np.floating)) else v)
                            for k, v in cal.items() if k != "major_dir"},
        }
        if write_intermediates and out_dir_path:
            (out_dir_path / f"{stem}_result.json").write_text(json.dumps(result_dict, indent=2))
        return result_dict

    warped, warp_meta = wrap_warp(crop, cal)
    image_1024, norm_meta = to_llm_square(warped, cal, warp_meta, bbox)
    invert_err_px = self_test_inversion(norm_meta, cal)

    result = detector.detect(image_1024, target_type=target_type, caliber_hint=caliber_hint)

    holes_crop: list[tuple[float, float]] = []
    holes_src: list[tuple[float, float]] = []
    for h in result.holes:
        xy_crop = norm_to_crop((float(h.x), float(h.y)), norm_meta)
        xy_src = crop_to_source(xy_crop, norm_meta)
        holes_crop.append(xy_crop)
        holes_src.append(xy_src)

    synthetic_r = max(3.0, 0.15 * float(cal["s_px"]))
    holes_crop_with_r = [(xy[0], xy[1], synthetic_r) for xy in holes_crop]
    classical_scores = score_holes(holes_crop_with_r, cal)

    result_dict = {
        "image": image_path.name,
        "ok": True,
        "detector": result.detector_name,
        "target_type": result.target_type,
        "caliber_hint": caliber_hint,
        "crop_bbox": [int(v) for v in bbox],
        "calibration": {
            "ok": bool(cal["ok"]),
            "cx": float(cal["cx"]),
            "cy": float(cal["cy"]),
            "r_bw_px": float(cal["r_bw_px"]),
            "r_bull_px": float(cal["r_bull_px"]),
            "s_px": float(cal["s_px"]),
            "anisotropy": float(cal["anisotropy"]),
            "peaks_aligned": int(cal.get("peaks_aligned", 0)),
        },
        "norm_meta": {
            "scale": float(norm_meta.scale),
            "tx": float(norm_meta.tx),
            "ty": float(norm_meta.ty),
            "size": int(norm_meta.size),
            "target_ring1_px": 500.0,
            "r_ring1_warped": float(norm_meta.r_ring1_warped),
        },
        "self_test": {
            "bullseye_invert_err_px": float(invert_err_px),
            "passed": bool(invert_err_px < 2.0),
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

    if write_intermediates and out_dir_path:
        llm_scores = [h.score for h in result.holes]
        cv2.imwrite(str(out_dir_path / f"{stem}_03_warp.png"), cv2.cvtColor(warped, cv2.COLOR_GRAY2BGR))
        cv2.imwrite(str(out_dir_path / f"{stem}_04_llm_input.png"), cv2.cvtColor(image_1024, cv2.COLOR_GRAY2BGR))
        cv2.imwrite(str(out_dir_path / f"{stem}_05_llm_predict.png"),
                    _draw_magenta_on_gray(image_1024, [(h.x, h.y) for h in result.holes], with_score=llm_scores))
        cv2.imwrite(str(out_dir_path / f"{stem}_06_crop_predict.png"),
                    deliverable(crop, cal, holes_crop_with_r, classical_scores))
        cv2.imwrite(str(out_dir_path / f"{stem}_07_source_predict.png"),
                    _draw_magenta_on_bgr(bgr, holes_src, with_score=llm_scores))
        (out_dir_path / f"{stem}_result.json").write_text(json.dumps(result_dict, indent=2))

    return result_dict
