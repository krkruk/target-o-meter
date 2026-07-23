"""Port interfaces for the vision domain.

Defines the strategy seam collaborators depend on. Implementations live in
``detectors/`` (concrete ``HoleDetector`` strategies) and ``geometry/``
(the deterministic pipeline). Only DTOs cross the domain boundary to the BFF
(AGENTS.md §5).

Lifted from ``cv/detector_base.py:71-101`` and ``:20`` (commit 76f6fc4) — the
locked Phase-3 Step-2 contract the production detectors already speak.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal, Optional

import numpy as np

if TYPE_CHECKING:  # avoid runtime cycle (detectors/detection_result imports ports)
    from src.domains.vision.detectors.detection_result import DetectionResult

TargetType = Literal["air_pistol", "precision_pistol"]


class HoleDetector(ABC):
    """Strategy interface for hole detection on a 1024x1024 normalized ISSF target image.

    The detector ONLY sees the normalized image + metadata hints, and returns
    hole positions + scores in the 1024x1024 frame. All geometry (localization,
    calibration, warp, coordinate inversion) is handled outside the strategy —
    so swapping detectors never breaks the transform chain.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in result JSONs and logs."""

    @abstractmethod
    def detect(
        self,
        image_1024: np.ndarray,
        target_type: TargetType,
        caliber_hint: Optional[str] = None,
        target_ring1_px: Optional[float] = None,
    ) -> "DetectionResult":
        """Run detection on a 1024x1024 normalized target image.

        Args:
            image_1024: uint8 grayscale, shape (1024, 1024). Bullseye at (512, 512).
            target_type: ``"air_pistol"`` | ``"precision_pistol"``.
            caliber_hint: optional string like ``"9x19"`` / ``"22lr"`` / ``".223Rem"`` / ``"slug"``.
            target_ring1_px: radius of the outermost (ring-1) printed ring from
                the bullseye, in 1024-frame px. Needed by the LLM detector to
                build its prompt (numeric ring step) and to size caliber→px
                magenta markers. ``None`` for detectors that ignore geometry
                (MockDetector). Phase 3 Step-2 handoff subtlety #1.

        Returns:
            ``DetectionResult`` with holes in 1024x1024 frame + LLM-provided scores.
        """
