"""Pydantic schema for the Gemma 4 31B-it structured output (Phase 3 spike).

Design (locked in the Step-1 interview):

- Top-level object, NOT a bare ``list`` — open VLMs (incl. Gemma 4) emit a
  top-level object more reliably than a bare array, and LangChain's
  ``with_structured_output`` has a known issue (#24225) failing on bare
  ``List`` types with ``ChatGoogleGenerativeAI``. The smoke test confirmed
  ``Dots(dots=[...])`` parses cleanly on Gemma 4 31B-it.
- Caliber is PER-HOLE (a target may carry mixed calibers — e.g. train image
  31). It is a free-text ``str``, not an enum, so specifics like
  "9x18 Makarov" / "9x17" remain admissible; the prompt lists the 6 canonical
  forms as guidance. Caliber is used only to size the magenta dot in Step 2
  (70% of hole diameter) — never for scoring.
- ``confidence`` is the per-hole fidelity metric the user asked for.
- ``score`` is ISSF 0..10; X (center hit) is reported as 10. The LLM scores
  authoritatively (Phase-1 Q5); metadata.yml is the comparison reference.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Hole(BaseModel):
    """One detected bullet hole in the 1024x1024 normalized frame."""

    x: int = Field(
        ..., ge=0, le=1024,
        description="Pixel x of the hole CENTER in the 1024x1024 image. "
                    "Bullseye is at x=512.",
    )
    y: int = Field(
        ..., ge=0, le=1024,
        description="Pixel y of the hole CENTER in the 1024x1024 image. "
                    "Bullseye is at y=512.",
    )
    score: int = Field(
        ..., ge=0, le=10,
        description="ISSF ring score 0..10. A hit touching a higher-value ring "
                    "line is awarded the higher value (line-break rule). "
                    "An X (inner-ten / center hit) is reported as 10.",
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="Your confidence 0..1 that this is a real bullet hole "
                    "(not a ring stroke, digit, fold, or shadow).",
    )
    caliber: str = Field(
        ...,
        description="Most likely cartridge caliber for THIS hole as free text. "
                    "Prefer the user-suggested primary caliber unless this hole "
                    "is clearly a different size. Canonical forms: "
                    "22lr, .223Rem, 9mm (9x19), .45ACP, 7.62x39, 12-gauge. "
                    "Specific variants like '9x18 Makarov' are admissible. "
                    "Note: 22lr and .223Rem are similar in diameter — pick one.",
    )


class TargetAnalysis(BaseModel):
    """Top-level structured output wrapping the per-hole list.

    Returned by ``ChatGoogleGenerativeAI.with_structured_output(TargetAnalysis)``.
    """

    holes: list[Hole] = Field(
        ..., description="Every bullet hole you can identify, largest/most "
                         "certain first. Omit ring strokes, printed digits, "
                         "paper folds, and shadows.",
    )
    target_type: Literal["air_pistol", "precision_pistol"] = Field(
        ..., description="Target type given to you in the prompt.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Optional one-line observation (e.g. ambiguous holes, "
                    "damage, mixed calibers). None if nothing notable.",
    )
