"""Single-ellipse pipeline orchestration.

For each input image, produce 9 files (8 PNGs + 1 JSON) under out_dir:
  <id>_01_intake.png        EXIF-oriented source image
  <id>_02_crop.png          after crop_to_target
  <id>_02b_detect.png       KEY DIAGNOSTIC: crop + Canny edges (red) + the fit
                            black-disc ellipse (yellow) + optional disambiguator
                            ring ellipses (cyan/green)
  <id>_03_warp.png          after inverse-perspective warp
  <id>_04_llm_input.png     1024×1024 normalized image
  <id>_05_llm_predict.png   1024 + magenta dots at MockDetector's fixed positions
  <id>_06_crop_predict.png  crop + inverted magenta dots + ring overlay
  <id>_07_source_predict.png source + fully-inverted magenta dots
  <id>_result.json          structured output

Deterministic geometry; the only variable is the MockDetector's fixed 5-hole
pattern.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from cv.approaches.singleellipse.blackdisc import decompose, fit_disc
from cv.approaches.singleellipse.localize import crop_to_target, find_disc
from cv.approaches.singleellipse.normalize import (
    NormMeta, crop_to_source, norm_to_source, norm_to_warped, self_test_inversion,
    to_llm_square, warped_to_crop,
)
from cv.approaches.singleellipse.warp import (
    apply_warp, build_homography, resolve_front_back, warp_with_refinement,
)
from cv.blob_detect import (
    calibrate, deliverable, draw_rings_overlay, ring_radii_px, score_holes,
    to_gray, _sobel_mag,
)
from cv.detector_base import DetectionResult, DetectedHole, HoleDetector, TargetType
from cv.gt import load_bgr
from cv.mock_detector import MockDetector


def _draw_ellipse(img: np.ndarray, cx: float, cy: float, a: float, b: float,
                  angle_deg: float, color, thickness: int = 2) -> None:
    """Draw an ellipse on `img` (in place). a, b are the OpenCV axes (full lengths)."""
    cv2.ellipse(img, (int(cx), int(cy)), (int(a), int(b)), int(angle_deg),
                0, 360, color, thickness)


def _diagnostic_overlay(
    crop_gray: np.ndarray,
    disc: dict,
    decomposition: dict,
) -> np.ndarray:
    """Build the _02b_detect.png diagnostic: crop + Canny edges + disc ellipse."""
    bgr = cv2.cvtColor(crop_gray, cv2.COLOR_GRAY2BGR)
    # Canny edges in red.
    edges = cv2.Canny(crop_gray, 80, 160)
    bgr[edges > 0] = (0, 0, 255)
    # Black-disc ellipse in yellow. fitEllipse returns axes as full lengths.
    (ec, er), (ea, eb), ang = cv2.fitEllipse(disc["contour"])
    _draw_ellipse(bgr, ec, er, ea, eb, ang, (0, 255, 255), thickness=3)
    # Major-axis line (decomposition.tilt_direction_deg) in green.
    cx, cy = decomposition["cx"], decomposition["cy"]
    phi = math.radians(decomposition["tilt_direction_deg"])
    sa = decomposition["semi_a"]
    cv2.line(bgr,
             (int(cx - sa * math.cos(phi)), int(cy - sa * math.sin(phi))),
             (int(cx + sa * math.cos(phi)), int(cy + sa * math.sin(phi))),
             (0, 255, 0), 2)
    # Note: no concentric ring ellipses are used in the single-ellipse approach.
    cv2.putText(bgr, f"theta={decomposition['tilt_magnitude_deg']:.1f} deg",
                (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    cv2.putText(bgr, f"phi={decomposition['tilt_direction_deg']:.0f} deg",
                (15, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    return bgr


def _draw_warped_ring_overlay(
    warped_gray: np.ndarray,
    disc_center_warped: tuple[float, float],
    r_bw_warped: float,
    r_bull_warped: float,
    s_px_warped: float,
) -> np.ndarray:
    """Draw concentric rings on the warped image (should be circular)."""
    bgr = cv2.cvtColor(warped_gray, cv2.COLOR_GRAY2BGR)
    cx, cy = disc_center_warped
    for k in range(10, 0, -1):
        r = r_bull_warped + (10 - k) * s_px_warped
        col = (0, 255, 255) if k == 10 else (0, 200, 0) if k == 7 else (60, 200, 60)
        thick = 2 if k in (1, 7, 10) else 1
        cv2.circle(bgr, (int(cx), int(cy)), int(r), col, thick)
    cv2.circle(bgr, (int(cx), int(cy)), 5, (0, 0, 255), -1)
    return bgr


def _draw_magenta_on_gray(gray: np.ndarray, points: list[tuple[float, float]],
                          radius: int = 12, with_score: Optional[list[int]] = None) -> np.ndarray:
    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for i, (x, y) in enumerate(points):
        cv2.circle(bgr, (int(x), int(y)), radius, (255, 0, 255), -1)
        if with_score is not None and i < len(with_score):
            cv2.putText(bgr, str(with_score[i]), (int(x) + radius + 2, int(y) + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 255), 2)
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


def _draw_crop_predict(
    crop_gray: np.ndarray,
    decomposition: dict,
    holes_crop: list[tuple[float, float]],
) -> np.ndarray:
    """Crop image + extrapolated ring ellipses (in source frame, anisotropic)
    + magenta inverted holes."""
    bgr = cv2.cvtColor(crop_gray, cv2.COLOR_GRAY2BGR)
    cx, cy = decomposition["cx"], decomposition["cy"]
    a = decomposition["semi_a"]
    b = decomposition["semi_b"]
    phi = math.radians(decomposition["tilt_direction_deg"])
    # Draw extrapolated ring ellipses using the source-frame disc geometry.
    # r_bw = semi_a (the black/white boundary ≈ the disc edge), and use ISSF
    # geometry: r_bull = r_bw / 7.391, s = (r_bw - r_bull)/3.
    r_bw_px = a
    r_bull_px = r_bw_px / 7.391
    s_px = (r_bw_px - r_bull_px) / 3.0
    for k in range(10, 0, -1):
        r = r_bull_px + (10 - k) * s_px
        # Source-frame ellipse axes: major = r, minor = r * (b/a) (perspective)
        axes_a = int(r)
        axes_b = int(r * b / a)
        col = (0, 255, 255) if k == 10 else (0, 200, 0) if k == 7 else (60, 200, 60)
        thick = 2 if k in (1, 7, 10) else 1
        cv2.ellipse(bgr, (int(cx), int(cy)), (axes_a, axes_b),
                    int(decomposition["tilt_direction_deg"]), 0, 360, col, thick)
    cv2.circle(bgr, (int(cx), int(cy)), 5, (0, 0, 255), -1)
    for (x, y) in holes_crop:
        cv2.circle(bgr, (int(x), int(y)), 12, (255, 0, 255), -1)
    return bgr


def run_pipeline(
    image_path: Path,
    detector: HoleDetector,
    target_type: TargetType = "air_pistol",
    caliber_hint: Optional[str] = None,
    out_dir: Optional[Path] = None,
    write_intermediates: bool = True,
    margin_factor: float = 5.5,
    out_radius_factor: float = 5.0,
) -> dict:
    """Run the single-ellipse pipeline on one image; return the result dict."""
    bgr = load_bgr(image_path)
    gray = to_gray(bgr)
    stem = image_path.stem
    out_dir_path = Path(out_dir) if out_dir else None
    if write_intermediates and out_dir_path:
        out_dir_path.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_dir_path / f"{stem}_01_intake.png"), bgr)

    # ------------------------------------------------------------------
    # Stage 1: localize (find disc on source, then crop with padding if the
    # disc is near an image edge — keeps inverted holes in frame for steep
    # tilts where the warp pushes "up" / "down" beyond the source bounds).
    # ------------------------------------------------------------------
    disc_src = find_disc(gray)
    if disc_src is None:
        result = {"image": image_path.name, "ok": False, "failure_stage": "find_disc"}
        if write_intermediates and out_dir_path:
            (out_dir_path / f"{stem}_result.json").write_text(json.dumps(result, indent=2))
        return result

    h, w = gray.shape
    scx, scy = float(disc_src["cx"]), float(disc_src["cy"])
    half = int(margin_factor * float(disc_src["semi_a"]))
    # Padding so that margin_factor × disc radius is available on every side
    # of the disc center, even when near an image edge. The padded area is
    # filled with paper-white (245) so the warp doesn't see a hard boundary.
    pad_l = max(0, half - int(scx))
    pad_r = max(0, half - (w - 1 - int(scx)))
    pad_t = max(0, half - int(scy))
    pad_b = max(0, half - (h - 1 - int(scy)))
    if pad_l or pad_r or pad_t or pad_b:
        gray_padded = cv2.copyMakeBorder(
            gray, pad_t, pad_b, pad_l, pad_r,
            cv2.BORDER_CONSTANT, value=245,
        )
        bgr_padded = cv2.copyMakeBorder(
            bgr, pad_t, pad_b, pad_l, pad_r,
            cv2.BORDER_CONSTANT, value=(245, 245, 245),
        )
        scx_pad = scx + pad_l
        scy_pad = scy + pad_t
    else:
        gray_padded = gray
        bgr_padded = bgr
        scx_pad = scx
        scy_pad = scy

    h_p, w_p = gray_padded.shape
    x0 = max(0, int(scx_pad - half)); y0 = max(0, int(scy_pad - half))
    x1 = min(w_p, int(scx_pad + half)); y1 = min(h_p, int(scy_pad + half))
    crop = gray_padded[y0:y1, x0:x1]
    bbox = (x0 - pad_l, y0 - pad_t, x1 - x0, y1 - y0)  # bbox in ORIGINAL source coords
    bbox_padded = (x0, y0, x1 - x0, y1 - y0)            # bbox in padded coords

    if write_intermediates and out_dir_path:
        cv2.imwrite(str(out_dir_path / f"{stem}_02_crop.png"),
                    cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR))

    # ------------------------------------------------------------------
    # Stage 2: fit disc on crop (hinted by source-level disc) + decompose.
    # hint coordinates are in the crop frame (= padded - crop_offset).
    # ------------------------------------------------------------------
    hint = {**disc_src, "cx": scx_pad - x0, "cy": scy_pad - y0}
    disc = fit_disc(crop, hint=hint)
    decomposition = decompose(disc, crop.shape)
    sign, sign_score, sign_reason = resolve_front_back(
        decomposition["cx"], decomposition["cy"],
        decomposition["semi_a"], decomposition["semi_b"],
        decomposition["tilt_direction_deg"],
        decomposition["focal_length_estimate"],
    )
    decomposition["tilt_sign"] = int(sign)
    decomposition["front_back_resolved_via"] = sign_reason

    if write_intermediates and out_dir_path:
        diag = _diagnostic_overlay(crop, disc, decomposition)
        cv2.imwrite(str(out_dir_path / f"{stem}_02b_detect.png"), diag)

    # ------------------------------------------------------------------
    # Stage 3: build inverse-perspective homography and warp (with iterative
    # refinement for large-tilt cases like image 21).
    # ------------------------------------------------------------------
    warped, H_total, warp_extra = warp_with_refinement(
        crop, disc, decomposition, sign,
        n_iters=3, out_radius_factor=out_radius_factor,
    )
    out_size = warped.shape[0]
    H_total_inv = np.linalg.inv(H_total)
    # Disc center in warped frame is (out_size/2, out_size/2) by construction
    disc_center_warped = (float(out_size) / 2.0, float(out_size) / 2.0)
    disc_radius_warped = float(warp_extra["final_disc_radius_warped"])

    if write_intermediates and out_dir_path:
        cv2.imwrite(str(out_dir_path / f"{stem}_03_warp.png"),
                    cv2.cvtColor(warped, cv2.COLOR_GRAY2BGR))

    # ------------------------------------------------------------------
    # Stage 4: normalize to 1024×1024
    # ------------------------------------------------------------------
    from cv.approaches.singleellipse.normalize import WarpMeta
    warp_meta = WarpMeta(H_total=H_total, H_total_inv=H_total_inv, out_size=int(out_size))
    image_1024, norm_meta = to_llm_square(
        warped, warp_meta, disc_center_warped, disc_radius_warped, bbox,
    )

    invert_err = self_test_inversion(norm_meta, (decomposition["cx"], decomposition["cy"]))

    if write_intermediates and out_dir_path:
        cv2.imwrite(str(out_dir_path / f"{stem}_04_llm_input.png"),
                    cv2.cvtColor(image_1024, cv2.COLOR_GRAY2BGR))

    # ------------------------------------------------------------------
    # Stage 5: detect holes (mock), invert to crop + source
    # ------------------------------------------------------------------
    result = detector.detect(image_1024, target_type=target_type, caliber_hint=caliber_hint)

    holes_norm = [(float(h.x), float(h.y)) for h in result.holes]
    holes_crop = [warped_to_crop(norm_to_warped(hn, norm_meta), norm_meta) for hn in holes_norm]
    holes_src = [crop_to_source(hc, norm_meta) for hc in holes_crop]

    # Classical scores from crop-frame positions using a synthetic radius.
    # Build a cal dict compatible with score_holes (cx, cy, r_bull_px, s_px).
    r_bw_px_crop = decomposition["semi_a"]
    r_bull_px_crop = r_bw_px_crop / 7.391
    s_px_crop = (r_bw_px_crop - r_bull_px_crop) / 3.0
    cal_compat = {
        "ok": True, "cx": decomposition["cx"], "cy": decomposition["cy"],
        "r_bw_px": r_bw_px_crop, "r_bull_px": r_bull_px_crop, "s_px": s_px_crop,
    }
    synthetic_r = max(3.0, 0.15 * s_px_crop)
    classical_scores = score_holes(
        [(hc[0], hc[1], synthetic_r) for hc in holes_crop], cal_compat,
    )

    # ------------------------------------------------------------------
    # Stage 6: write deliverables
    # ------------------------------------------------------------------
    llm_scores = [int(h.score) for h in result.holes]
    if write_intermediates and out_dir_path:
        # _05_llm_predict.png
        cv2.imwrite(str(out_dir_path / f"{stem}_05_llm_predict.png"),
                    _draw_magenta_on_gray(image_1024, holes_norm, with_score=llm_scores))
        # _06_crop_predict.png
        cv2.imwrite(str(out_dir_path / f"{stem}_06_crop_predict.png"),
                    _draw_crop_predict(crop, decomposition, holes_crop))
        # _07_source_predict.png
        cv2.imwrite(str(out_dir_path / f"{stem}_07_source_predict.png"),
                    _draw_magenta_on_bgr(bgr, holes_src, with_score=llm_scores))

    result_dict = {
        "image": image_path.name,
        "ok": True,
        "approach": "singleellipse",
        "detector": result.detector_name,
        "target_type": result.target_type,
        "caliber_hint": caliber_hint,
        "crop_bbox": [int(v) for v in bbox],
        "disc_source": {
            "cx": float(disc_src["cx"]), "cy": float(disc_src["cy"]),
            "semi_a": float(disc_src["semi_a"]), "semi_b": float(disc_src["semi_b"]),
            "anisotropy": float(disc_src["anisotropy"]),
            "circularity": float(disc_src["circularity"]),
        },
        "disc_crop": {
            "cx": float(decomposition["cx"]), "cy": float(decomposition["cy"]),
            "semi_a": float(decomposition["semi_a"]),
            "semi_b": float(decomposition["semi_b"]),
            "anisotropy": float(decomposition["anisotropy"]),
        },
        "decomposition": {
            "tilt_magnitude_deg": float(decomposition["tilt_magnitude_deg"]),
            "tilt_direction_deg": float(decomposition["tilt_direction_deg"]),
            "focal_length_estimate": float(decomposition["focal_length_estimate"]),
            "front_back_resolved_via": decomposition["front_back_resolved_via"],
            "tilt_sign": int(decomposition["tilt_sign"]),
        },
        "warp": {
            "H_total": [[float(v) for v in row] for row in H_total],
            "out_size": int(out_size),
            "disc_radius_warped": float(disc_radius_warped),
        },
        "norm_meta": {
            "bbox": [int(v) for v in norm_meta.bbox],
            "out_size": int(norm_meta.out_size),
            "scale": float(norm_meta.scale),
            "tx": float(norm_meta.tx),
            "ty": float(norm_meta.ty),
            "size": int(norm_meta.size),
            "disc_center_warped": list(norm_meta.disc_center_warped),
            "r_bw_warped": float(norm_meta.r_bw_warped),
            "r_bull_warped": float(norm_meta.r_bull_warped),
            "s_px_warped": float(norm_meta.s_px_warped),
            "calibrate_ok": bool(norm_meta.calibrate_ok),
        },
        "self_test": {
            "bullseye_invert_err_px": float(invert_err),
            "passed": bool(invert_err < 0.01),
        },
        "holes_norm": [{"x": h.x, "y": h.y, "score": h.score} for h in result.holes],
        "holes_crop": [{"x": float(xy[0]), "y": float(xy[1])} for xy in holes_crop],
        "holes_src": [{"x": float(xy[0]), "y": float(xy[1])} for xy in holes_src],
        "scores_llm": llm_scores,
        "scores_classical": [int(s) for s in classical_scores],
        "count": len(result.holes),
        "total_llm": int(sum(llm_scores)),
        "total_classical": int(sum(classical_scores)),
        "notes": result.notes,
    }

    if write_intermediates and out_dir_path:
        (out_dir_path / f"{stem}_result.json").write_text(json.dumps(result_dict, indent=2))

    return result_dict
