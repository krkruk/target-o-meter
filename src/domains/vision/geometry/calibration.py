"""Typed value object replacing the untyped ``cal`` dict from cv/.

The cv/ pipeline builds a ``cal`` dict at
``cv/approaches/full_pipeline/pipeline.py:361-368`` with the keys
``{ok, shape, cx, cy, s_px, r_bull_px, r_bw_px}`` and reads it in every
downstream stage. Phases 2–4 of the cv-service-boundary change port those
stages into classes that accept/return this typed ``Calibration`` instead of
the dict, so the signatures are clean from day one.

Defining ``Calibration`` in Phase 1 (before any geometry class) avoids a
retrofit pass later — see plan §Critical Implementation Details.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Calibration:
    """Geometric calibration of a single crop.

    Field names mirror the cv/ ``cal`` dict verbatim so the math ports
    one-for-one. ``shape`` stays a tuple (H, W) — ``BlackDiscCalibrator`` and
    pipeline consumers read it as ``cal.shape[:2]``.
    """

    shape: tuple[int, int] | tuple[int, int, int]
    cx: float
    cy: float
    s_px: float
    r_bull_px: float
    r_bw_px: float
    ok: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Calibration":
        """Build from a cv/ ``cal`` dict (the legacy shape produced by
        ``BlackDiscCalibrator`` / the pipeline's fallback path).

        Unknown keys are ignored; missing required keys raise ``KeyError``.
        """
        return cls(
            shape=tuple(d["shape"]),
            cx=float(d["cx"]),
            cy=float(d["cy"]),
            s_px=float(d["s_px"]),
            r_bull_px=float(d["r_bull_px"]),
            r_bw_px=float(d["r_bw_px"]),
            ok=bool(d.get("ok", True)),
        )
