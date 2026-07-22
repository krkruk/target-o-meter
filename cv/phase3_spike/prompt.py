"""Layered system-prompt builder for the Phase 3 LLM spike.

Structure (7 layers, per the interview in research-ai-detection.md):

    0. message shape: SystemMessage (this) is stable across calls;
       HumanMessage carries only the image + a one-line instruction.
    1. Role & objective
    2. Coordinate frame & geometry   (bullseye (512,512), 10 rings, ring step injected)
    3. What a hole IS                 (positive definition)
    4. What a hole is NOT             (negatives — the classical failure modes)
    5. ISSF scoring                   (line-break rule, 0..10, X=10)
    6. Caliber inference              (per-hole; primary hint; 6 canonical forms)
    7. Output contract                (JSON per schema)

Runtime variables injected per image:
    - target_type ("air_pistol" | "precision_pistol")
    - ring_step_px (numeric; = target_ring1_px / 9, the load-bearing value)
    - target_ring1_px (numeric; radius of the outermost ring from bullseye)
    - primary_caliber (str | None; simulates the UI-collected user hint)

Q5 (geometry injection): defaults chosen in the interview — describe ring 1
qualitatively AND give the numeric ring step. The absolute ring-1 radius is
mentioned as a secondary anchor. Subject to prompt tuning later.
"""
from __future__ import annotations

from typing import Optional

from cv.detector_base import TargetType

# Canonical caliber forms for the prompt. Free-text caliber is allowed in the
# schema, but these are the ones we name explicitly.
_CANONICAL_CALIBERS = ["22lr", ".223Rem", "9mm", ".45ACP", "7.62x39", "12-gauge"]


def build_system_prompt(
    target_type: TargetType,
    target_ring1_px: float,
    ring_step_px: Optional[float] = None,
    primary_caliber: Optional[str] = None,
) -> str:
    """Build the 7-layer system message.

    Args:
        target_type: "air_pistol" or "precision_pistol".
        target_ring1_px: radius of the outermost (ring-1) printed ring from the
            bullseye, in 1024-frame pixels. Varies per image (Phase 2.5
            dropped the fixed ring1_px=500 convention; range observed on the
            train set: ~321..394 px).
        ring_step_px: distance between consecutive rings in 1024-frame px.
            Defaults to target_ring1_px / 9 (10 rings => 9 steps to ring 1).
            This is the load-bearing value for scoring.
        primary_caliber: the user-suggested primary caliber (in production the
            UI collects this; here we inject metadata.yml's value to simulate
            the flow). None means "not provided".
    """
    ring_step_px = ring_step_px if ring_step_px is not None else target_ring1_px / 9.0
    caliber_hint_block = (
        f"The user reports the primary caliber is **{primary_caliber}**. "
        "Prefer this for most holes unless a hole is clearly a different size."
        if primary_caliber
        else "No primary caliber was provided — infer each hole's size independently."
    )

    return f"""You are an ISSF target scorer. Your job: find EVERY bullet hole on this paper target and report its pixel center, ISSF ring score, a confidence value, and a caliber guess.

# 0. Critical scanning discipline
Do NOT focus only on the center or the black disc. Systematically scan the ENTIRE image edge-to-edge, including:
- The four corners and all four edges of the frame.
- The area OUTSIDE the outermost ring (ring 1) — holes there score 0 but are still real holes and MUST be reported.
- The regions between rings, where holes are easily missed.
Hits can land anywhere in the frame, not just near the bullseye. Missed holes are worse than false positives.

# 1. Coordinate frame & geometry
- The image is a 1024x1024 pixel, fronto-parallel (top-down, undistorted) view of a paper shooting target.
- The BULLSEYE (center of the target, ring 10) is exactly at pixel (512, 512).
- There are 10 concentric scoring rings. Ring 1 is the OUTERMOST printed ring, fully visible inside the frame; ring 10 is the innermost (the bullseye).
- The distance between consecutive rings is approximately **{ring_step_px:.1f} pixels**. Ring 1 lies at about **{target_ring1_px:.0f} pixels** from the bullseye.
- Lower ring numbers are farther from center (ring 1 = ~{target_ring1_px:.0f}px out); higher numbers are closer (ring 10 = the center).

# 2. What counts as a bullet hole
A bullet hole is a roughly circular tear in the paper: typically darker than the surrounding paper, with ragged/irregular edges and often a faint lighter-toned halo. Its diameter scales with caliber (a 22lr hole is small; a 12-gauge slug hole is large). Mark only the CENTER of each hole.

**Double hits and grazing hits are possible.** Two bullets may pass through the same spot or graze an existing hole, producing a single larger, irregular, or lobed tear. When a tear looks noticeably larger, elongated, or multi-lobed relative to the expected single-hole size for the caliber, it is very likely TWO (or more) overlapping hits — report each as a separate hole (their centers may be very close together). Do NOT merge overlapping holes into one.

# 3. What is NOT a bullet hole (do not report these)
- **Pasties / repair stickers / patches (IMPORTANT).** Shooters cover old holes with adhesive patches so the target can be reused. These patches are rectangular, oval, or circular, are typically the SAME color as the area they cover (white on white paper, black on the black disc) or solid white, and have SMOOTH, clean edges (unlike ragged bullet holes). A reliable cue: **a patch is LARGER than a real bullet hole** (it must cover the hole), so a same-color same-tone shape noticeably larger than the caliber should NOT be reported as a hole. Black patches on the black aim disc are especially common and easily mistaken for holes — ignore them.
- Ring strokes (the printed circle lines themselves) — the classical detector's worst failure mode.
- Printed ring numbers / digits (1..9, X, etc.).
- Paper folds, creases, and wrinkles.
- Shadows (from fingers, staples, lighting).
- The black aim disc / the central black area as a whole (that is ink, not a hole).
- Smudges, stains, and printing artefacts.

# 4. ISSF scoring (line-break rule)
Score each hole 0..10 by which ring its center falls in. A hole that TOUCHES a higher-value ring line is awarded the HIGHER value. An X (inner ten / center hit) is reported as **10**. Lower score = farther from the bullseye. Count 0 if a hole lies outside ring 1.

# 5. Caliber inference (per hole)
{caliber_hint_block}
For each hole, give its single most likely caliber as free text. Canonical forms: {", ".join(_CANONICAL_CALIBERS)}. Specific variants (e.g. "9x18 Makarov", "9x17") are admissible. Note: **22lr and .223Rem are similar in diameter — choose one**; prefer the primary caliber when in doubt. Caliber is only used to size a marker, never for scoring.

# 6. Target type
You are scoring a **{target_type}** target. Both target types share the same 1024x1024 normalized frame and 10-ring layout described above.

# 7. Output contract
Return ONLY the JSON object described by the schema: an object with a "holes" array, "target_type", and optional "notes". Each hole needs integer x, y in [0,1024], integer score in [0,10], float confidence in [0,1], and string caliber. List holes most certain first. Do not include any prose outside the JSON."""


def build_user_text() -> str:
    """The single instruction line sent in the HumanMessage alongside the image."""
    return ("Examine this 1024x1024 normalized target image. Scan the ENTIRE "
            "frame edge-to-edge (corners and outside-ring-1 area too) and find "
            "every bullet hole. Watch for pasties/stickers to ignore, and for "
            "double/grazing hits to split. Return the JSON described in the "
            "system instructions.")
