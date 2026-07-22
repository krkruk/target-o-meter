"""Helpers: load metadata.yml and the fused result.json (for ring geometry).

Pure data access — no LLM, no network. Imported by both the Step-1 standalone
harness and (later) the Step-2 pipeline integration.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_METADATA_YML = _REPO_ROOT / "resources" / "paper_targets" / "metadata.yml"

# Mapping between the caliber strings that appear in metadata.yml and the
# canonical 6-form caliber list used in the Phase 3 spike. metadata.yml uses
# older/specific labels; the LLM is free to emit either form (free-text in the
# schema), but for GT comparison we normalize.
METADATA_CALIBER_ALIASES = {
    "9x19": "9mm",
    "slug": "12-gauge",  # train images 10/21/22/23/36/37/38 use 'slug'
}


def normalize_caliber(c: str) -> str:
    """Normalize a caliber string for comparison. Lowercase, alias-resolved.

    '9x19' -> '9mm', 'slug' -> '12-gauge'; others pass through lowercased.
    """
    if c is None:
        return ""
    c = str(c).strip()
    return METADATA_CALIBER_ALIASES.get(c, c)


def load_metadata() -> dict:
    """Load resources/paper_targets/metadata.yml.

    Returns a dict keyed by image stem (e.g. "12") -> {"hits": [...], "caliber": ...}.
    Caliber may be a string or (for mixed-caliber targets like 31) a list; we
    keep the raw value and expose helpers below.
    """
    with _METADATA_YML.open() as f:
        raw = yaml.safe_load(f)
    out: dict[str, dict] = {}
    for key, val in raw.items():
        stem = str(key).split(".")[0]  # "12.jpg" -> "12"
        out[stem] = val if isinstance(val, dict) else {"hits": [], "caliber": None}
    return out


def primary_caliber_for(meta_entry: dict) -> str | None:
    """The primary caliber to inject as the UI-style hint.

    For single-caliber images: the caliber string. For mixed (list): the first.
    For missing: None.
    """
    cal = meta_entry.get("caliber")
    if isinstance(cal, list):
        return normalize_caliber(cal[0]) if cal else None
    if isinstance(cal, str) and cal.strip():
        return normalize_caliber(cal)
    return None


def gt_hits_for(meta_entry: dict) -> list[int]:
    """Ground-truth score multiset for an image (sorted ascending)."""
    hits = meta_entry.get("hits") or []
    return sorted(int(h) for h in hits)


def load_fused_result(stem: str, fused_dir: Path | None = None) -> dict | None:
    """Load the fused pipeline's <stem>_result.json to recover ring geometry.

    We need ``norm_meta.target_ring1_px`` (the radius of the outermost ring in
    the 1024 frame) so the LLM prompt can mention it and the numeric ring step.
    """
    if fused_dir is None:
        fused_dir = _REPO_ROOT / "resources" / "train" / "intermediate_fused"
    path = fused_dir / f"{stem}_result.json"
    if not path.exists():
        return None
    import json
    with path.open() as f:
        return json.load(f)


def ring1_px_for(stem: str, fused_dir: Path | None = None) -> float | None:
    """Extract target_ring1_px from a fused result.json, or None if absent."""
    res = load_fused_result(stem, fused_dir)
    if not res:
        return None
    nm = res.get("norm_meta") or {}
    val = nm.get("target_ring1_px")
    return float(val) if val is not None else None
