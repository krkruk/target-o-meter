"""Contract collection for the geometry pipeline's result value objects.

Per the one-class-per-file rule (``lessons.md``) the geometry subpackage's
worker classes each previously co-located their ``@dataclass`` return type —
but those dataclasses are consumed across modules (``RefinementResult`` is
read by ``GeometryPipeline``; ``GeometryResult`` is read by ``PipelineRunner``
and the regression test), which the lesson's "serves only that class"
carve-out explicitly disqualifies.

This module is the explicit contract-collection exception — the same status
``ports.py`` and ``dtos.py`` already enjoy. It hosts only the *Result
dataclasses; worker classes import them from here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from src.domains.vision.geometry.calibration import Calibration
from src.domains.vision.geometry.coordinate_frame import CoordinateFrame


@dataclass
class RingDetection:
    """Shape returned by ``RingDetector.detect`` — preserved as the cv/ dict so
    downstream code reads ``r["cx"], r["semi_a"]`` etc. unchanged."""

    rings: list[dict]
    edges: np.ndarray      # uint8 Canny overlay (diagnostic)
    clahe: np.ndarray      # uint8 CLAHE-equalized crop (diagnostic)


@dataclass
class RectificationResult:
    """Shape returned by ``CircularPointsRectifier.compute`` — preserved as the
    cv/ dict so downstream code reads keys unchanged."""

    H: np.ndarray
    H_inv: np.ndarray
    Q: np.ndarray
    center: np.ndarray
    circular_points: tuple[complex, complex]
    center_drift: np.ndarray | None
    used_projective: bool


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
