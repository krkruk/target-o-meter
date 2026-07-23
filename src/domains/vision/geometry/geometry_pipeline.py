"""Compose stages 1–8 of the cv/ fused pipeline (intake → localize → rings →
H_init → refine → warp → normalize) into one class that returns the
LLM-ready image + geometry.

Ported from ``cv/approaches/full_pipeline/pipeline.py:241-453`` (the
geometry-only part — stages 9–end are ``PipelineRunner``'s job in Phase 4).
The cv/ pipeline-level helpers (``_is_plausible_cal``, ``_elliptical_band_mask``,
``_mean_ring_eccentricity``, ``_warped_ring_metrics``) are private methods
here.

Math is lifted as-is; structure changes only to use the ported classes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from src.domains.vision.geometry.adaptive_frame_sizer import AdaptiveFrameSizer
from src.domains.vision.geometry.calibration import Calibration
from src.domains.vision.geometry.circular_points_rectifier import (
    CircularPointsRectifier,
    _average_shared_metric,
)
from src.domains.vision.geometry.classical_stages import (
    BlackDiscCalibrator,
    ImageGrayscaler,
)
from src.domains.vision.geometry.coordinate_frame import CoordinateFrame
from src.domains.vision.geometry.homography_model import HomographyModel
from src.domains.vision.geometry.homography_refiner import (
    FusedHomographyRefiner,
    RefinementResult,
)
from src.domains.vision.geometry.image_loader import ImageLoader
from src.domains.vision.geometry.normalizer import Normalizer
from src.domains.vision.geometry.ring_detector import RingDetector
from src.domains.vision.geometry.target_localizer import TargetLocalizer
from src.domains.vision.geometry.warp_projector import WarpProjector
from src.domains.vision.ports import TargetType


@dataclass
class GeometryResult:
    """Everything the detector + renderer need from the geometry pass."""

    bgr: np.ndarray
    gray: np.ndarray
    image_1024: np.ndarray
    target_ring1_px: float
    coordinate_frame: CoordinateFrame
    calibration: Calibration
    refinement: RefinementResult
    frame_info: dict
    metrics: dict[str, Any]
    bbox: tuple[int, int, int, int]
    rings: list[dict]
    debug_artifacts: dict[str, np.ndarray] = field(default_factory=dict)


class GeometryPipeline:
    """Run stages 1–8 on one image. Returns a ``GeometryResult``.

    The ``debug`` flag (when True) populates ``GeometryResult.debug_artifacts``
    with the 14-file Phase-2.5 diagnostic manifest. Default False keeps the
    result lean.
    """

    @staticmethod
    def _is_plausible_cal(s_px: float, r_bw_px: float, r_bull_px: float) -> bool:
        """Sanity check for ISSF target calibration values.
        cv/approaches/full_pipeline/pipeline.py:68-81."""
        if not (s_px > 5.0 and r_bw_px > 0.0 and r_bull_px > 0.0):
            return False
        if not (3.0 * s_px < r_bw_px < 15.0 * s_px):
            return False
        if not (0.0 < r_bull_px < r_bw_px):
            return False
        return True

    @staticmethod
    def _elliptical_band_mask(
        crop_shape: tuple[int, int], rings: list[dict], band_factor: float = 0.3,
    ) -> np.ndarray:
        """Boolean mask: True where pixel is within ±``band_factor``·gmean-radius
        of any detected ring's ellipse.
        cv/approaches/full_pipeline/pipeline.py:84-106."""
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
            rx = cos_t * dx + sin_t * dy
            ry = -sin_t * dx + cos_t * dy
            r_eff = np.sqrt((rx / a) ** 2 + (ry / b) ** 2)
            mask |= np.abs(r_eff - 1.0) < band_factor
        return mask

    @staticmethod
    def _mean_ring_eccentricity(rings: list[dict]) -> float:
        """Mean ``semi_a / semi_b`` across detected rings.
        cv/approaches/full_pipeline/pipeline.py:109-114."""
        if not rings:
            return 1.0
        eccs = [float(r["semi_a"]) / max(float(r["semi_b"]), 1e-6) for r in rings]
        return float(np.mean(eccs))

    @staticmethod
    def _warped_ring_metrics(
        rings: list[dict], H: np.ndarray,
    ) -> tuple[float, float, float, np.ndarray]:
        """Compute ``(s_warped, r_bull_warped, r_ring1_warped, center_warped)``
        by transforming the detected rings through H.
        cv/approaches/full_pipeline/pipeline.py:117-162."""
        if not rings:
            raise ValueError("no rings")

        _, center, _ = _average_shared_metric(rings)
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

        order = np.argsort(rms_radii)
        rms_sorted = [rms_radii[i] for i in order]
        max_sorted = [max_radii[i] for i in order]

        r_bull_warped = rms_sorted[0]
        r_ring1_warped = max_sorted[-1]
        if len(rms_sorted) >= 2:
            gaps = [rms_sorted[i + 1] - rms_sorted[i]
                    for i in range(len(rms_sorted) - 1)
                    if rms_sorted[i + 1] > rms_sorted[i]]
            s_warped = float(np.median(gaps)) if gaps else r_ring1_warped / 9.0
        else:
            s_warped = r_ring1_warped / 9.0

        return s_warped, r_bull_warped, r_ring1_warped, center_warped

    def run(
        self,
        image_path: Path,
        *,
        target_type: TargetType = "air_pistol",
        gt_marked_path: Path | None = None,
        projective_refine_init: bool = False,
        debug: bool = False,
    ) -> GeometryResult:
        bgr = ImageLoader.load_bgr(image_path)
        gray = ImageGrayscaler.to_gray(bgr)

        # ---- Stage 1: localize (multiring, logo-rejecting) ----
        crop, bbox, init = TargetLocalizer.crop_to_target(gray)
        cx_crop = float(init["cx_crop"])
        cy_crop = float(init["cy_crop"])
        s_px = float(init.get("s_px_init") or 0)
        r_bw_px = float(init.get("r_bw_px_init") or 0)
        r_bull_px_init = float(init.get("r_bull_px_init") or 0)
        if r_bull_px_init <= 0 and s_px > 0 and r_bw_px > 0:
            r_bull_px_init = r_bw_px - 3.0 * s_px
        cal_source = "multiring_init"

        if not self._is_plausible_cal(s_px, r_bw_px, r_bull_px_init):
            try:
                bd_cal = BlackDiscCalibrator.calibrate(crop)
                if bd_cal.ok:
                    s_bd = bd_cal.s_px
                    r_bw_bd = bd_cal.r_bw_px
                    r_bull_bd = bd_cal.r_bull_px
                    if r_bull_bd <= 0 and s_bd > 0 and r_bw_bd > 0:
                        r_bull_bd = r_bw_bd - 3.0 * s_bd
                    if self._is_plausible_cal(s_bd, r_bw_bd, r_bull_bd):
                        s_px, r_bw_px, r_bull_px_init = s_bd, r_bw_bd, r_bull_bd
                        cal_source = "bd_calibrate_fallback"
            except Exception:
                pass

        # ---- Stage 2: detect rings (multiring) ----
        det = RingDetector.detect(crop, init=init)
        rings = det.rings

        if len(rings) < 2 or s_px <= 0 or r_bull_px_init <= 0:
            raise ValueError(
                f"geometry pipeline could not bootstrap: rings={len(rings)} "
                f"(need ≥2), s_px={s_px:.2f}, r_bull_px_init={r_bull_px_init:.2f}"
            )

        # ---- Stage 3: initial H via circular-points (AFFINE) ----
        hres = CircularPointsRectifier.compute(
            rings, projective_refine=projective_refine_init,
        )
        H_init = hres.H
        M2 = H_init[:2, :2]
        t_vec = H_init[:2, 2]
        aff_init = HomographyModel.affine_init_params(M2, t_vec)
        ocx_init_arr = M2 @ np.array([cx_crop, cy_crop]) + t_vec
        ocx_init, ocy_init = float(ocx_init_arr[0]), float(ocx_init_arr[1])

        s_warped_init, r_bull_warped_init, r_ring1_warped_init, _ = (
            self._warped_ring_metrics(rings, H_init)
        )

        mean_ecc = self._mean_ring_eccentricity(rings)
        is_orthogonal = mean_ecc < 1.05
        perspective_bound = 1e-5 if is_orthogonal else 1e-4

        band_mask = self._elliptical_band_mask(crop.shape, rings, band_factor=0.3)

        cal = Calibration(
            shape=crop.shape,
            cx=cx_crop, cy=cy_crop,
            s_px=s_px,
            r_bull_px=r_bull_px_init,
            r_bw_px=r_bw_px,
            ok=True,
        )

        # ---- Stage 4: differential refinement (iteredge-style 8-DOF) ----
        opt = FusedHomographyRefiner.refine(
            gray_crop=crop, cal=cal,
            affine_init_params=aff_init, affine_M2=M2, affine_t=t_vec,
            warped_out_center=(ocx_init, ocy_init),
            s_warped=s_warped_init, r_bull_warped=r_bull_warped_init,
            perspective_bound=perspective_bound,
            edge_band_mask=band_mask,
            corner_gate_enable=True,
            mean_ring_eccentricity=mean_ecc,
        )
        H_opt = opt.final_H

        # ---- Stage 5: warp with refined H ----
        margin_factor, frame_info = AdaptiveFrameSizer.margin_factor(
            bbox=bbox,
            H_opt=H_opt,
            cx_crop=cx_crop,
            cy_crop=cy_crop,
            r_ring1_warped=r_ring1_warped_init,
            gt_marked_path=gt_marked_path,
        )
        out_w, out_h, H_full = WarpProjector.compute_output_shape(
            H_opt, crop.shape, cx_crop, cy_crop, r_ring1_warped_init,
            margin_factor=margin_factor,
        )
        warped = WarpProjector.apply_warp(crop, H_full, (out_w, out_h))
        bullseye_warped = (out_w / 2.0, out_h / 2.0)

        # ---- Stage 6: normalize to 1024 — fit the ENTIRE warp canvas, no cropping
        scale_for_1024 = 1024.0 / max(out_w, out_h)
        target_ring1_px = float(r_ring1_warped_init) * scale_for_1024

        image_1024, meta = Normalizer.normalize_to_1024(
            warped=warped,
            H_full=H_full,
            bullseye_warped=bullseye_warped,
            bbox=bbox,
            r_ring1_warped=r_ring1_warped_init,
            cx_crop=cx_crop, cy_crop=cy_crop,
            target_ring1_px=target_ring1_px,
        )

        metrics = {
            "mean_ring_eccentricity": float(mean_ecc),
            "is_orthogonal": bool(is_orthogonal),
            "perspective_bound": float(perspective_bound),
            "cal_source": cal_source,
            "init_homography_projective": bool(hres.used_projective),
            "bullseye_invert_err_px": meta.self_test_inversion(),
            "r_ring1_warped": float(r_ring1_warped_init),
            "s_warped": float(s_warped_init),
            "r_bull_warped": float(r_bull_warped_init),
        }

        debug_artifacts: dict[str, np.ndarray] = {}
        if debug:
            debug_artifacts = {
                "clahe": det.clahe,
                "edges": det.edges,
                "warped": warped,
            }

        return GeometryResult(
            bgr=bgr,
            gray=gray,
            image_1024=image_1024,
            target_ring1_px=target_ring1_px,
            coordinate_frame=meta,
            calibration=cal,
            refinement=opt,
            frame_info=frame_info,
            metrics=metrics,
            bbox=bbox,
            rings=rings,
            debug_artifacts=debug_artifacts,
        )
