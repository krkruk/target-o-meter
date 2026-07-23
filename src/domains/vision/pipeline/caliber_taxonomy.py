"""Caliber normalization + diameter lookup.

Ported from the metadata-half of ``cv/phase3_spike/metadata.py`` (the
``METADATA_CALIBER_ALIASES`` map + ``normalize_caliber``) plus the
``_CALIBER_DIAMETER_MM`` table that lived in ``cv/phase3_spike/viz.py``.

Consolidated here per plan §4.1 (single source of truth) so the renderer does
not depend on eval tooling.
"""
from __future__ import annotations


# Aliases between metadata.yml labels and the canonical 6-form list.
# metadata.yml uses older/specific labels; the LLM is free to emit either form
# (free-text in the schema), but for GT comparison we normalize.
METADATA_CALIBER_ALIASES = {
    "9x19": "9mm",
    "slug": "12-gauge",  # train images 10/21/22/23/36/37/38 use 'slug'
}


# Nominal bullet diameters (mm). Used to size magenta markers.
# .22lr and .223Rem are nearly identical (5.7 vs 5.56) — both map near 5.6 mm.
# Ported from cv/phase3_spike/viz.py:31-38.
CALIBER_DIAMETER_MM: dict[str, float] = {
    "22lr": 5.7,
    ".223rem": 5.56,
    "9mm": 9.01,
    ".45acp": 11.5,
    "7.62x39": 7.9,
    "12-gauge": 18.0,  # slug; shot-cup spread varies, slug is ~18 mm
}

DEFAULT_DIAMETER_MM = 9.0  # fallback when the LLM's caliber string is unrecognized


class CaliberTaxonomy:
    """Normalize caliber strings + look up nominal bullet diameters."""

    @staticmethod
    def normalize(c: str | None) -> str:
        """Normalize a caliber string for comparison. Lowercase, alias-resolved.

        ``9x19`` → ``9mm``, ``slug`` → ``12-gauge``; others pass through
        lowercased. Ported verbatim from cv/phase3_spike/metadata.py:25-33.
        """
        if c is None:
            return ""
        c = str(c).strip()
        return METADATA_CALIBER_ALIASES.get(c, c)

    @staticmethod
    def diameter_mm(c: str | None) -> float:
        """Nominal bullet diameter in mm for the given caliber string.

        Falls back to ``DEFAULT_DIAMETER_MM`` when the string is unrecognized.
        """
        cal = CaliberTaxonomy.normalize(c).lower()
        return CALIBER_DIAMETER_MM.get(cal, DEFAULT_DIAMETER_MM)
