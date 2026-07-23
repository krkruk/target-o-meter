"""Pydantic schema for the structured output (locked Step-1 contract).

Ported verbatim from ``cv/phase3_spike/schema.py`` (79 LOC at commit 76f6fc4).
Field constraints and descriptions are load-bearing prompt-steering — they
must not change.
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

    Returned by ``ChatGoogleGenerativeAI.with_structured_output(TargetAnalysis)``
    (Google) or ``ChatOllama.with_structured_output(TargetAnalysis)`` (Ollama).
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
