"""``PipelineRunner`` — wires geometry + detector + renderer into the 3-file
deliverable writer.

Ported from stages 9–end of ``cv/approaches/full_pipeline/pipeline.py:444-547``
(commit 76f6fc4). After ``GeometryPipeline`` produces ``image_1024``, the
runner calls ``detector.detect(...)``, inverts holes to crop/source frames,
computes classical scores (diagnostic), and writes the 3 deliverables:

    <stem>_llm_input.png   the 1024×1024 normalized orthogonal LLM input
    <stem>_marked.png      llm_input + magenta dots (∝ caliber, 70% of hole)
                           + faint canonical ring frame + score labels
    <stem>_result.json     the LLM structured output + target_type + notes
                           + ring geometry

The 14-file Phase-2.5 diagnostics stay gated behind ``debug=True`` (handled
inside ``GeometryPipeline.run(debug=True)``).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from src.domains.vision.geometry.issf_scorer import IssfScorer
from src.domains.vision.geometry.geometry_pipeline import GeometryPipeline
from src.domains.vision.pipeline.deliverable_renderer import DeliverableRenderer
from src.domains.vision.ports import HoleDetector, TargetType


logger = logging.getLogger(__name__)


class PipelineRunner:
    """Compose ``GeometryPipeline`` + ``HoleDetector`` + ``DeliverableRenderer``
    into the 3-file deliverable writer.

    The ``target_ring1_px`` computed by ``GeometryPipeline`` flows geometry →
    detector (the sanctioned Step-2 subtlety #1).
    """

    def __init__(self, detector: HoleDetector) -> None:
        self._detector = detector
        self._geometry = GeometryPipeline()

    def run(
        self,
        image_path: Path,
        *,
        target_type: TargetType = "air_pistol",
        caliber_hint: Optional[str] = None,
        out_dir: Optional[Path] = None,
        debug: bool = False,
        gt_marked_path: Optional[Path] = None,
    ) -> dict:
        """Run the full pipeline on one image; optionally write 3 deliverables.

        Returns the ``result_dict`` (the cv/ shape at pipeline.py:486-542).
        """
        image_path = Path(image_path)
        stem = image_path.stem
        out_path = Path(out_dir) if out_dir else None
        if out_path:
            out_path.mkdir(parents=True, exist_ok=True)

        geometry = self._geometry.run(
            image_path,
            target_type=target_type,
            gt_marked_path=gt_marked_path,
            debug=debug,
        )

        target_ring1_px = float(geometry.target_ring1_px)
        cal = geometry.calibration
        meta = geometry.coordinate_frame

        # ---- WRITE DELIVERABLE (a): _llm_input.png ----
        if out_path:
            cv2.imwrite(
                str(out_path / f"{stem}_llm_input.png"),
                cv2.cvtColor(geometry.image_1024, cv2.COLOR_GRAY2BGR),
            )

        # ---- Stage 7: detect (LLM via the seam) ----
        # target_ring1_px flows geometry → detector (handoff subtlety #1).
        result = self._detector.detect(
            geometry.image_1024,
            target_type=target_type,
            caliber_hint=caliber_hint,
            target_ring1_px=target_ring1_px,
        )

        holes_crop: list[tuple[float, float]] = []
        holes_src: list[tuple[float, float]] = []
        for h in result.holes:
            xy_crop = meta.norm_to_crop(float(h.x), float(h.y))
            xy_src = meta.crop_to_source_xy(*xy_crop)
            holes_crop.append(xy_crop)
            holes_src.append(xy_src)

        synthetic_r = max(3.0, 0.15 * float(cal.s_px))
        holes_crop_with_r = [(xy[0], xy[1], synthetic_r) for xy in holes_crop]
        classical_score_fallback = False
        try:
            classical_scores = IssfScorer.score_holes(holes_crop_with_r, cal)
        except Exception:
            logger.warning(
                "IssfScorer.score_holes failed; substituting LLM scores for "
                "classical scores (diff_total will be 0 for this image)",
                exc_info=True,
            )
            classical_score_fallback = True
            classical_scores = [h.score for h in result.holes]
        llm_scores = [int(h.score) for h in result.holes]

        # ---- WRITE DELIVERABLE (b): _marked.png ----
        if out_path:
            holes_dump = [h.to_dict() for h in result.holes]
            marked = DeliverableRenderer.draw_magenta_holes(
                image_1024_gray=geometry.image_1024,
                holes=holes_dump,
                target_type=target_type,
                target_ring1_px=target_ring1_px,
            )
            cv2.imwrite(str(out_path / f"{stem}_marked.png"), marked)

        # ---- Build result dict (the cv/ shape at pipeline.py:486-542) ----
        result_dict = {
            "image": image_path.name,
            "ok": True,
            "approach": "vision_pipeline",
            "detector": result.detector_name,
            "target_type": result.target_type,
            "caliber_hint": caliber_hint,
            "crop_bbox": [int(v) for v in geometry.bbox],
            "calibration": {
                "cx": float(cal.cx), "cy": float(cal.cy),
                "r_bw_px": float(cal.r_bw_px),
                "r_bull_px": float(cal.r_bull_px),
                "s_px": float(cal.s_px),
                "source": geometry.metrics["cal_source"],
            },
            "initial_homography": {
                "method": "multiring_circular_points_affine",
                "projective_refine_used": bool(geometry.metrics["init_homography_projective"]),
            },
            "refinement": {
                "parameterization": "homography_8dof",
                "final_cost": float(geometry.refinement.final_cost),
                "n_iterations": int(geometry.refinement.n_iterations),
                "converged": bool(geometry.refinement.converged),
                "n_stages": len(geometry.refinement.stages),
                "reverted_to_init": bool(geometry.refinement.reverted_to_init),
                "revert_reason": geometry.refinement.revert_reason,
                "mean_ring_eccentricity": float(geometry.metrics["mean_ring_eccentricity"]),
                "is_orthogonal": bool(geometry.metrics["is_orthogonal"]),
                "perspective_bound": float(geometry.metrics["perspective_bound"]),
                "defense_layer": geometry.refinement.defense_layer,
            },
            "adaptive_frame": geometry.frame_info,
            "norm_meta": {
                "scale": float(meta.scale),
                "tx": float(meta.tx), "ty": float(meta.ty),
                "size": int(meta.size),
                "target_ring1_px": float(target_ring1_px),
                "r_ring1_warped": float(meta.r_ring1_warped),
                "bullseye_warped": list(meta.bullseye_warped),
            },
            "self_test": {
                "bullseye_invert_err_px": float(geometry.metrics["bullseye_invert_err_px"]),
                "passed": bool(geometry.metrics["bullseye_invert_err_px"] < 0.01),
            },
            "holes": [h.to_dict() for h in result.holes],
            "holes_norm": [h.to_dict() for h in result.holes],
            "holes_crop": [{"x": float(xy[0]), "y": float(xy[1])} for xy in holes_crop],
            "holes_src": [{"x": float(xy[0]), "y": float(xy[1])} for xy in holes_src],
            "scores_llm": llm_scores,
            "scores_classical": [int(s) for s in classical_scores],
            "classical_score_fallback": classical_score_fallback,
            "count": len(result.holes),
            "total_llm": int(sum(llm_scores)),
            "total_classical": int(sum(classical_scores)),
            "notes": result.notes,
            "detector_raw": result.raw,
        }

        # ---- WRITE DELIVERABLE (c): _result.json ----
        if out_path:
            (out_path / f"{stem}_result.json").write_text(
                json.dumps(result_dict, indent=2, default=json_default),
            )

        return result_dict


def json_default(obj):
    """Pydantic/numpy-aware JSON fallback for the result_dict writer."""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    raise TypeError(f"Unserializable: {type(obj)!r}")
