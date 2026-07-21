"""End-to-end fused pipeline.

Stage sequence:
    1. intake         EXIF-aware load
    2. localize       multiring (logo-rejecting)
    3. detect rings   multiring (bounded 4-param ellipse fit per ring)
    4. initial H      multiring (circular-points method, AFFINE only — perspective left to refiner)
    5. refine         iteredge-style 8-DOF differential fitting against edge DT
                      (4 coarse-to-fine stages, callback writes per-stage PNG)
    6. warp           iteredge (compute_output_shape + apply_warp)
    7. adaptive frame fused (target_ring1_px from GT hole extent if available)
    8. normalize      iteredge (normalize_to_1024)
    9. detect (mock)  cv.mock_detector.MockDetector (5-hole pattern in 1024 coords)
   10. invert + viz   full forward-inverse roundtrip + 9 PNG outputs + per-stage intermediates
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from cv.approaches.fused.adaptive_frame import adaptive_margin_factor
from cv.approaches.fused.refine import refine_homography
from cv.approaches.iteredge.edges import canny_edges
from cv.approaches.iteredge.model import (
    affine_init_params,
    apply_H_to_points,
    params_to_H,
    ring_points_warped,
)
from cv.approaches.iteredge.normalize import (
    norm_to_crop,
    crop_to_source_xy,
    norm_to_source,
    self_test_inversion,
    normalize_to_1024,
)
from cv.approaches.iteredge.warp import apply_warp, compute_output_shape
from cv.approaches.multiring.detect_rings import detect_rings
from cv.approaches.multiring.homography import (
    average_shared_metric,
    compute_rectifying_homography,
)
from cv.approaches.multiring.localize import crop_to_target
from cv.blob_detect import (
    calibrate as bd_calibrate,
    score_holes,
    to_gray,
)
from cv.detector_base import DetectionResult, HoleDetector, TargetType
from cv.gt import load_bgr


def _is_plausible_cal(s_px: float, r_bw_px: float, r_bull_px: float) -> bool:
    """Sanity check for ISSF target calibration values.

    On a real target: r_bw ≈ 7·s (the black/white boundary is the 4-ring
    outer = 7 steps from the 10-ring), r_bull ≈ r_bw − 3·s (bullseye is the
    10-ring outer = 3 steps inside the BW boundary). Both must be positive.
    """
    if not (s_px > 5.0 and r_bw_px > 0.0 and r_bull_px > 0.0):
        return False
    if not (3.0 * s_px < r_bw_px < 15.0 * s_px):
        return False
    if not (0.0 < r_bull_px < r_bw_px):
        return False
    return True


def _elliptical_band_mask(
    crop_shape: tuple[int, int],
    rings: list[dict],
    band_factor: float = 0.3,
) -> np.ndarray:
    """Boolean mask: True where pixel is within ±band_factor·gmean-radius of
    any detected ring's ellipse.

    Used to filter Canny edges before computing DT for the differential
    fitter — keeps only edges that lie on or near detected ring strokes,
    rejecting digit edges, hole edges, and background clutter that
    otherwise pull the optimizer off the true rings (overfitting root
    cause on images 1 and 4).

    The mask is computed in CROP frame (where multiring's rings live).
    """
    h, w = crop_shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    mask = np.zeros((h, w), dtype=bool)
    for r in rings:
        cx, cy = float(r["cx"]), float(r["cy"])
        a = max(float(r["semi_a"]), 1.0)
        b = max(float(r["semi_b"]), 1.0)
        th = math.radians(r["angle_deg"])
        cos_t, sin_t = math.cos(th), math.sin(th)
        dx = xx - cx
        dy = yy - cy
        # Rotate into ellipse-axis frame
        rx = cos_t * dx + sin_t * dy
        ry = -sin_t * dx + cos_t * dy
        # Effective elliptical radius: 1.0 on the ellipse, <1 inside, >1 outside
        r_eff = np.sqrt((rx / a) ** 2 + (ry / b) ** 2)
        mask |= np.abs(r_eff - 1.0) < band_factor
    return mask


def _mean_ring_eccentricity(rings: list[dict]) -> float:
    """Mean semi_a / semi_b across detected rings. 1.0 = perfectly circular;
    values near 1 indicate an orthogonal source (rings already circular in
    crop frame). Used to detect orthogonal images and tighten perspective
    bounds accordingly.
    """
    if not rings:
        return 1.0
    eccs = [float(r["semi_a"]) / max(float(r["semi_b"]), 1e-6) for r in rings]
    return float(np.mean(eccs))


def _warped_ring_metrics(rings: list[dict], H: np.ndarray) -> tuple[float, float, float, np.ndarray]:
    """Compute (s_warped, r_bull_warped, r_ring1_warped, center_warped) by
    transforming the detected rings through H and measuring their radii in
    the warped frame around the warped bullseye.

    This is the correct way to size ring_points_warped: we need WARPED-frame
    radii, not crop-frame ones. Iteredge's own pipeline conflates the two,
    which works for it because blob_detect's affine H is near-identity. For
    our fused pipeline, multiring's H_init applies Q^{-1/2} which scales
    rings significantly — so we MUST compute warped values explicitly.

    Returns:
        s_warped: median gap between consecutive warped ring RMS radii.
        r_bull_warped: smallest warped ring RMS radius (≈ the 9-ring outer).
        r_ring1_warped: MAX sample-radius of the outermost ring under H
            (NOT the RMS). For an elliptical ring 1, the far side from the
            bullseye extends well beyond the RMS — using MAX here ensures
            the frame sizing accounts for that far side so it doesn't get
            clipped at the 1024 frame edge. Image 46 root cause.
        center_warped: 2-vec, where multiring's averaged ring center lands
            under H (the warped-frame bullseye).
    """
    if not rings:
        raise ValueError("no rings")

    _, center, _ = average_shared_metric(rings)
    center_homog = H @ np.array([center[0], center[1], 1.0], dtype=np.float64)
    if abs(center_homog[2]) < 1e-12:
        center_homog[2] = 1e-12
    center_warped = center_homog[:2] / center_homog[2]

    rms_radii: list[float] = []
    max_radii: list[float] = []
    for r in rings:
        th = math.radians(r["angle_deg"])
        ca, sa = math.cos(th), math.sin(th)
        a, b = r["semi_a"], r["semi_b"]
        pts = np.array([
            [r["cx"] + a * ca * math.cos(k) - b * sa * math.sin(k),
             r["cy"] + a * sa * math.cos(k) + b * ca * math.sin(k)]
            for k in np.linspace(0, 2 * math.pi, 36, endpoint=False)
        ], dtype=np.float64)
        homog = np.hstack([pts, np.ones((pts.shape[0], 1))])
        mapped = (H @ homog.T).T
        mapped = mapped[:, :2] / mapped[:, 2:3]
        d = np.hypot(mapped[:, 0] - center_warped[0],
                     mapped[:, 1] - center_warped[1])
        rms_radii.append(float(np.sqrt(np.mean(d * d))))
        max_radii.append(float(np.max(d)))

    # Order rings by RMS (robust to single-point outliers), then take the MAX
    # sample-radius for the outermost ring as r_ring1_warped.
    order = np.argsort(rms_radii)
    rms_sorted = [rms_radii[i] for i in order]
    max_sorted = [max_radii[i] for i in order]

    r_bull_warped = rms_sorted[0]
    r_ring1_warped = max_sorted[-1]  # MAX of outermost ring (frame-sizing)
    if len(rms_sorted) >= 2:
        gaps = [rms_sorted[i + 1] - rms_sorted[i]
                for i in range(len(rms_sorted) - 1)
                if rms_sorted[i + 1] > rms_sorted[i]]
        s_warped = float(np.median(gaps)) if gaps else r_ring1_warped / 9.0
    else:
        s_warped = r_ring1_warped / 9.0

    return s_warped, r_bull_warped, r_ring1_warped, center_warped


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------
_RING_COLORS = [
    (0, 0, 255), (0, 165, 255), (0, 255, 255), (0, 255, 0), (255, 255, 0),
    (255, 0, 0), (255, 0, 255), (128, 0, 128), (0, 128, 255), (255, 128, 0),
]


def _draw_detect_overlay(crop_gray: np.ndarray, edges: np.ndarray,
                         rings: list[dict]) -> np.ndarray:
    """Crop with red Canny edges + colored fitted ellipses (multiring style)."""
    bgr = cv2.cvtColor(crop_gray, cv2.COLOR_GRAY2BGR)
    edges_bgr = np.zeros_like(bgr)
    edges_bgr[..., 2] = edges
    bgr = cv2.addWeighted(bgr, 1.0, edges_bgr, 0.6, 0)
    for i, r in enumerate(rings):
        col = _RING_COLORS[i % len(_RING_COLORS)]
        axes = (int(round(r["semi_a"])), int(round(r["semi_b"])))
        cv2.ellipse(bgr, (int(r["cx"]), int(r["cy"])), axes,
                    int(r["angle_deg"]), 0, 360, col, 2)
        cv2.circle(bgr, (int(r["cx"]), int(r["cy"])), 3, col, -1)
        label = f"r{r.get('ring_value_estimate', '?')}"
        cv2.putText(bgr, label, (int(r["cx"]) + 6, int(r["cy"]) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
    return bgr


def _draw_ring_overlay_warped(warped_gray: np.ndarray, bullseye: tuple[float, float],
                              s_warped: float, n_rings: int = 10) -> np.ndarray:
    """Warped image with 10 concentric circles + bullseye dot."""
    bgr = cv2.cvtColor(warped_gray, cv2.COLOR_GRAY2BGR)
    cx, cy = bullseye
    for k in range(1, n_rings + 1):
        r = k * s_warped
        col = (0, 255, 255) if k == n_rings else (0, 200, 0) if k == 7 else (60, 200, 60)
        thick = 2 if k in (1, 7, n_rings) else 1
        cv2.circle(bgr, (int(cx), int(cy)), int(r), col, thick)
    cv2.circle(bgr, (int(cx), int(cy)), 5, (0, 0, 255), -1)
    return bgr


def _draw_stage_projection(crop_gray: np.ndarray, canny_bin: np.ndarray,
                            current_H: np.ndarray, ocx: float, ocy: float,
                            r_bull_warped: float, s_warped: float,
                            n_rings: int = 10, n_pts: int = 96) -> np.ndarray:
    """Per-stage diagnostic: crop + red Canny + green rings predicted under H.

    Green rings that drift OFF the red edges signal an overshoot at that stage.
    """
    bgr = cv2.cvtColor(crop_gray, cv2.COLOR_GRAY2BGR)
    bgr[canny_bin > 0] = (0, 0, 255)
    try:
        H_inv = np.linalg.inv(current_H)
    except np.linalg.LinAlgError:
        return bgr
    pts_warped = ring_points_warped(
        ocx=ocx, ocy=ocy, r_bull_warped=r_bull_warped, s_warped=s_warped,
        n_rings=n_rings, n_per_ring=n_pts,
    )
    pts_crop = apply_H_to_points(H_inv, pts_warped)
    for k in range(n_rings):
        chunk = pts_crop[k * n_pts:(k + 1) * n_pts]
        if len(chunk) >= 2:
            cv2.polylines(bgr, [chunk.astype(np.int32)], True, (0, 255, 0), 2)
    return bgr


def _draw_final_product(image_1024: np.ndarray, target_ring1_px: float,
                        holes: list[tuple[float, float]],
                        scores: list[int],
                        bullseye_xy: tuple[int, int] = (512, 512),
                        size: int = 1024) -> np.ndarray:
    """FINAL PRODUCT: 1024 LLM-input + canonical ring frame + magenta holes with scores.

    The canonical ring frame shows the geometric layout the LLM is told about
    (bullseye at (512, 512), 1-ring at radius target_ring1_px, ring step
    target_ring1_px/9). This is the single image the user inspects to verify
    both the warp quality and the detector output in one glance.
    """
    bgr = cv2.cvtColor(image_1024, cv2.COLOR_GRAY2BGR)
    s_1024 = float(target_ring1_px) / 9.0
    bcx, bcy = bullseye_xy
    for k in range(1, 11):
        r = int(round(k * s_1024))
        col = (0, 255, 255) if k == 10 else (0, 200, 0) if k == 7 else (60, 200, 60)
        thick = 2 if k in (1, 7, 10) else 1
        cv2.circle(bgr, (bcx, bcy), r, col, thick)
    cv2.circle(bgr, (bcx, bcy), 5, (0, 0, 255), -1)
    for (x, y), sc in zip(holes, scores):
        cv2.circle(bgr, (int(x), int(y)), 12, (255, 0, 255), -1)
        cv2.putText(bgr, str(sc), (int(x) + 14, int(y) + 5),
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
    gt_marked_path: Optional[Path] = None,
    projective_refine_init: bool = False,
) -> dict:
    """Run the fused pipeline on one image.

    Args:
      projective_refine_init: forwarded to multiring's
        compute_rectifying_homography. Should stay False — the differential
        refiner handles perspective terms; pre-baking them into the affine
        init just throws away information when affine_init_params discards
        H[2,:2].
    """
    bgr = load_bgr(image_path)
    gray = to_gray(bgr)

    stem = image_path.stem
    out_path = Path(out_dir) if out_dir else None
    if write_intermediates and out_path:
        out_path.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_path / f"{stem}_01_intake.png"), bgr)

    # ---- Stage 1: localize (multiring, logo-rejecting) ----
    crop, bbox, init = crop_to_target(gray)
    cx_crop = float(init["cx_crop"])
    cy_crop = float(init["cy_crop"])
    s_px = float(init.get("s_px_init") or 0)
    r_bw_px = float(init.get("r_bw_px_init") or 0)
    r_bull_px_init = float(init.get("r_bull_px_init") or 0)
    if r_bull_px_init <= 0 and s_px > 0 and r_bw_px > 0:
        r_bull_px_init = r_bw_px - 3.0 * s_px
    cal_source = "multiring_init"

    # Fallback to cv.blob_detect.calibrate (2-anchor radial-profile) when the
    # multiring init values are missing or implausible. Mirrors multiring's
    # own pipeline (cv/approaches/multiring/pipeline.py:174-184).
    if not _is_plausible_cal(s_px, r_bw_px, r_bull_px_init):
        try:
            bd_cal = bd_calibrate(crop)
            if bd_cal.get("ok"):
                s_bd = float(bd_cal.get("s_px", 0) or 0)
                r_bw_bd = float(bd_cal.get("r_bw_px", 0) or 0)
                r_bull_bd = float(bd_cal.get("r_bull_px", 0) or 0)
                if r_bull_bd <= 0 and s_bd > 0 and r_bw_bd > 0:
                    r_bull_bd = r_bw_bd - 3.0 * s_bd
                if _is_plausible_cal(s_bd, r_bw_bd, r_bull_bd):
                    s_px, r_bw_px, r_bull_px_init = s_bd, r_bw_bd, r_bull_bd
                    cal_source = "bd_calibrate_fallback"
        except Exception:
            pass

    if write_intermediates and out_path:
        cv2.imwrite(str(out_path / f"{stem}_02_crop.png"),
                    cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR))

    # ---- Stage 2: detect rings (multiring) ----
    det = detect_rings(crop, init=init)
    rings = det["rings"]
    edges_uint8 = det["edges"]

    if write_intermediates and out_path:
        cv2.imwrite(str(out_path / f"{stem}_02b_detect.png"),
                    _draw_detect_overlay(crop, edges_uint8, rings))

    if len(rings) < 2 or s_px <= 0 or r_bull_px_init <= 0:
        result_dict = {
            "image": image_path.name,
            "ok": False,
            "approach": "fused",
            "failure_stage": "detect_rings_or_init",
            "reason": (f"rings={len(rings)} (need ≥2), s_px={s_px:.2f}, "
                       f"r_bull_px_init={r_bull_px_init:.2f}"),
            "crop_bbox": [int(v) for v in bbox],
            "rings_detected": len(rings),
        }
        if write_intermediates and out_path:
            (out_path / f"{stem}_result.json").write_text(json.dumps(result_dict, indent=2))
        return result_dict

    # ---- Stage 3: initial H via circular-points (AFFINE — perspective left to refiner) ----
    try:
        hres = compute_rectifying_homography(rings, projective_refine=projective_refine_init)
    except Exception as exc:
        result_dict = {
            "image": image_path.name,
            "ok": False,
            "approach": "fused",
            "failure_stage": "initial_homography",
            "reason": str(exc),
            "crop_bbox": [int(v) for v in bbox],
            "rings_detected": len(rings),
        }
        if write_intermediates and out_path:
            (out_path / f"{stem}_result.json").write_text(json.dumps(result_dict, indent=2))
        return result_dict

    H_init = hres["H"]
    M2 = H_init[:2, :2]
    t_vec = H_init[:2, 2]
    aff_init = affine_init_params(M2, t_vec)
    ocx_init_arr = M2 @ np.array([cx_crop, cy_crop]) + t_vec
    ocx_init, ocy_init = float(ocx_init_arr[0]), float(ocx_init_arr[1])

    # Compute WARPED-frame ring metrics from the actual detected rings under
    # H_init. These are the radii ring_points_warped needs — they differ from
    # the crop-frame cal values when H_init rescales rings significantly
    # (multiring's Q^{-1/2} rectifier; image 12 rings shrink ~3×).
    s_warped_init, r_bull_warped_init, r_ring1_warped_init, center_warped_init = (
        _warped_ring_metrics(rings, H_init)
    )

    # Detect orthogonality: low ring eccentricity → source was nearly
    # frontal → multiring's affine H_init is already near-perfect → tighten
    # perspective bounds 10× further to prevent the optimizer from inventing
    # unnecessary perspective correction.
    mean_ecc = _mean_ring_eccentricity(rings)
    is_orthogonal = mean_ecc < 1.05
    perspective_bound = 1e-5 if is_orthogonal else 1e-4

    # Build the elliptical band mask for edge filtering. Only Canny edges
    # near detected ring strokes will drive the optimizer — rejects digits,
    # holes, background clutter that caused overfitting on images 1 and 4.
    band_mask = _elliptical_band_mask(crop.shape, rings, band_factor=0.3)

    # cal carries CROP-frame s_px/r_bull for enhance_ring_edges's blur/falloff.
    cal = {
        "ok": True,
        "shape": crop.shape,
        "cx": cx_crop, "cy": cy_crop,
        "s_px": s_px,
        "r_bull_px": r_bull_px_init,
        "r_bw_px": r_bw_px,
    }

    # ---- Stage 4: differential refinement (iteredge-style 8-DOF) ----
    canny_bin = canny_edges(crop)
    stage_images: list[np.ndarray] = []  # for the strip

    def stage_callback(stage_idx: int, current_params: np.ndarray,
                       current_H: np.ndarray, potential, info: dict) -> None:
        if not (write_intermediates and out_path):
            return
        viz = _draw_stage_projection(
            crop, canny_bin, current_H,
            ocx=ocx_init, ocy=ocy_init,
            r_bull_warped=r_bull_warped_init, s_warped=s_warped_init,
        )
        label = (f"stage {stage_idx}: {info.get('pot_kind', '?')} "
                 f"sigma={info.get('sigma', 0):.1f} "
                 f"cost={info.get('cost', float('nan')):.2e} "
                 f"nfev={info.get('nfev', 0)} det={info.get('det', 0):.3f}")
        if info.get("reverted_to_init"):
            label += " REVERTED"
        cv2.putText(viz, label, (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.imwrite(str(out_path / f"{stem}_08_stage{stage_idx}.png"), viz)
        stage_images.append(viz)

    opt = refine_homography(
        gray_crop=crop, cal=cal,
        affine_init_params=aff_init, affine_M2=M2, affine_t=t_vec,
        warped_out_center=(ocx_init, ocy_init),
        s_warped=s_warped_init, r_bull_warped=r_bull_warped_init,
        stage_callback=stage_callback,
        perspective_bound=perspective_bound,
        edge_band_mask=band_mask,
        corner_gate_enable=True,
    )
    H_opt = opt["final_H"]

    # ---- Stage 5: warp with refined H ----
    # Use the warped-derived r_ring1 (from actual detected rings) to size the
    # output frame, NOT the init's r_bull + 9*s — those are crop-frame and
    # would give a frame 3× too small (image 12 root cause).
    #
    # Adaptive margin_factor: enlarge the warp canvas when GT holes extend
    # beyond ring 1 (e.g. image 21 slugs at 1.31× ring 1) so the warp
    # doesn't crop them. Default 1.30 is fine when holes are inside ring 1.
    margin_factor, frame_info = adaptive_margin_factor(
        bbox=bbox,
        H_opt=H_opt,
        cx_crop=cx_crop,
        cy_crop=cy_crop,
        r_ring1_warped=r_ring1_warped_init,
        gt_marked_path=gt_marked_path,
    )
    out_w, out_h, H_full = compute_output_shape(
        H_opt, crop.shape, cx_crop, cy_crop, r_ring1_warped_init,
        margin_factor=margin_factor,
    )
    warped = apply_warp(crop, H_full, (out_w, out_h))
    bullseye_warped = (out_w / 2.0, out_h / 2.0)
    s_warped = float(s_warped_init)

    if write_intermediates and out_path:
        cv2.imwrite(str(out_path / f"{stem}_03_warp.png"),
                    _draw_ring_overlay_warped(warped, bullseye_warped, s_warped))

    # ---- Stage 6: normalize to 1024 — fit the ENTIRE warp canvas, no content cropping ----
    # scale is set so the whole warp canvas (out_w × out_h) fits inside 1024²
    # with bullseye centered. This GUARANTEES that whatever is visible in
    # _03_warp.png is also visible in _04_llm_input.png — the previous
    # regression was choosing target_ring1_px and cropping content that
    # fell outside it.
    scale_for_1024 = 1024.0 / max(out_w, out_h)
    target_ring1_px = float(r_ring1_warped_init) * scale_for_1024

    image_1024, meta = normalize_to_1024(
        warped=warped,
        H_full=H_full,
        bullseye_warped=bullseye_warped,
        bbox=bbox,
        r_ring1_warped=r_ring1_warped_init,
        cx_crop=cx_crop, cy_crop=cy_crop,
        target_ring1_px=target_ring1_px,
    )
    invert_err = self_test_inversion(meta)

    if write_intermediates and out_path:
        cv2.imwrite(str(out_path / f"{stem}_04_llm_input.png"),
                    cv2.cvtColor(image_1024, cv2.COLOR_GRAY2BGR))

    # ---- Stage 8: detect (mock) ----
    result: DetectionResult = detector.detect(
        image_1024, target_type=target_type, caliber_hint=caliber_hint,
    )

    holes_crop: list[tuple[float, float]] = []
    holes_src: list[tuple[float, float]] = []
    for h in result.holes:
        xy_crop = norm_to_crop(float(h.x), float(h.y), meta)
        xy_src = crop_to_source_xy(*xy_crop, meta)
        holes_crop.append(xy_crop)
        holes_src.append(xy_src)

    synthetic_r = max(3.0, 0.15 * float(cal["s_px"]))
    holes_crop_with_r = [(xy[0], xy[1], synthetic_r) for xy in holes_crop]
    try:
        classical_scores = score_holes(holes_crop_with_r, cal)
    except Exception:
        classical_scores = [h.score for h in result.holes]
    llm_scores = [int(h.score) for h in result.holes]

    # ---- Build result dict ----
    result_dict = {
        "image": image_path.name,
        "ok": True,
        "approach": "fused",
        "detector": result.detector_name,
        "target_type": result.target_type,
        "caliber_hint": caliber_hint,
        "crop_bbox": [int(v) for v in bbox],
        "calibration": {
            "cx": float(cal["cx"]), "cy": float(cal["cy"]),
            "r_bw_px": float(cal["r_bw_px"]),
            "r_bull_px": float(cal["r_bull_px"]),
            "s_px": float(cal["s_px"]),
            "source": cal_source,
        },
        "initial_homography": {
            "method": "multiring_circular_points_affine",
            "projective_refine_used": bool(hres["used_projective"]),
        },
        "refinement": {
            "parameterization": "homography_8dof",
            "final_cost": float(opt["final_cost"]),
            "final_H": [[float(x) for x in row] for row in opt["final_H"]],
            "n_iterations": int(opt["n_iterations"]),
            "converged": bool(opt["converged"]),
            "n_stages": len(opt["stages"]),
            "stages": [
                {k: (float(v) if isinstance(v, (int, float)) else v) for k, v in s.items()}
                for s in opt["stages"]
            ],
            "reverted_to_init": bool(opt.get("reverted_to_init", False)),
            "revert_reason": opt.get("revert_reason"),
            "init_data_score": float(opt.get("init_data_score", float("nan"))),
            "opt_data_score": float(opt.get("opt_data_score", float("nan"))),
            "mean_ring_eccentricity": float(mean_ecc),
            "is_orthogonal": bool(is_orthogonal),
            "perspective_bound": float(perspective_bound),
            "edge_band_mask_px": int(band_mask.sum()),
            "corner_ratio_init": opt.get("corner_ratio_init"),
            "corner_ratio_final": opt.get("corner_ratio_final"),
            "corner_ratio_gate": opt.get("corner_ratio_gate"),
        },
        "adaptive_frame": frame_info,
        "norm_meta": {
            "scale": float(meta.scale),
            "tx": float(meta.tx), "ty": float(meta.ty),
            "size": int(meta.size),
            "target_ring1_px": float(target_ring1_px),
            "r_ring1_warped": float(meta.r_ring1_warped),
            "bullseye_warped": list(meta.bullseye_warped),
        },
        "self_test": {
            "bullseye_invert_err_px": float(invert_err),
            "passed": bool(invert_err < 0.01),
        },
        "holes_norm": [h.to_dict() for h in result.holes],
        "holes_crop": [{"x": float(xy[0]), "y": float(xy[1])} for xy in holes_crop],
        "holes_src": [{"x": float(xy[0]), "y": float(xy[1])} for xy in holes_src],
        "scores_llm": llm_scores,
        "scores_classical": [int(s) for s in classical_scores],
        "count": len(result.holes),
        "total_llm": int(sum(llm_scores)),
        "total_classical": int(sum(classical_scores)),
        "notes": result.notes,
    }

    # ---- Write intermediates ----
    if write_intermediates and out_path:
        # _05_llm_predict.png — FINAL PRODUCT (LLM input + ring frame + magenta + scores)
        cv2.imwrite(str(out_path / f"{stem}_05_llm_predict.png"),
                    _draw_final_product(image_1024, target_ring1_px,
                                        [(float(h.x), float(h.y)) for h in result.holes],
                                        llm_scores))

        # _06_crop_predict.png — crop + ring overlay (under final H) + inverted holes
        crop_viz = _draw_stage_projection(
            crop, canny_bin, H_full,
            ocx=bullseye_warped[0], ocy=bullseye_warped[1],
            r_bull_warped=r_bull_warped_init, s_warped=s_warped_init,
        )
        for (x, y), sc in zip(holes_crop, classical_scores):
            cv2.circle(crop_viz, (int(x), int(y)), max(4, int(synthetic_r)),
                       (255, 0, 255), -1)
            cv2.putText(crop_viz, str(sc),
                        (int(x) + int(synthetic_r) + 2, int(y) + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 255), 2)
        cv2.imwrite(str(out_path / f"{stem}_06_crop_predict.png"), crop_viz)

        # _07_source_predict.png — source + fully-inverted holes
        cv2.imwrite(str(out_path / f"{stem}_07_source_predict.png"),
                    _draw_magenta_on_bgr(bgr, holes_src, with_score=llm_scores))

        # _08_stages_strip.png — all per-stage projections concatenated horizontally
        if stage_images:
            strip = np.hstack(stage_images)
            cv2.imwrite(str(out_path / f"{stem}_08_stages_strip.png"), strip)

        (out_path / f"{stem}_result.json").write_text(json.dumps(result_dict, indent=2))

    return result_dict
