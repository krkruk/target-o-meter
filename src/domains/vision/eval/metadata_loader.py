"""Loader for ``resources/paper_targets/metadata.yml``.

Ported verbatim from the metadata.yml-half of ``cv/phase3_spike/metadata.py``
(commit 76f6fc4). Diagnostic/test-only — NOT imported by ``services`` or
``models``.

The ``load_fused_result`` / ``ring1_px_for`` helpers from cv/ are NOT ported
(they read cv/ intermediate output that no longer exists in the domain —
``GeometryPipeline`` produces ``target_ring1_px`` directly now).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from src.domains.vision.pipeline.caliber_taxonomy import CaliberTaxonomy


_REPO_ROOT = Path(__file__).resolve().parents[4]
_METADATA_YML = _REPO_ROOT / "resources" / "paper_targets" / "metadata.yml"


class MetadataLoader:
    """Read accessors for ``resources/paper_targets/metadata.yml``.

    The yml is keyed by image filename (``"12.jpg"``); each value is a dict
    with ``hits`` (list of ISSF scores) and ``caliber`` (string or list for
    mixed-caliber targets like image 31).
    """

    @staticmethod
    def path() -> Path:
        """Repo-relative path to ``metadata.yml``."""
        return _METADATA_YML

    @staticmethod
    def load_metadata() -> dict:
        """Load ``resources/paper_targets/metadata.yml``.

        Returns a dict keyed by image stem (e.g. ``"12"``) →
        ``{"hits": [...], "caliber": ...}``.

        Ported from cv/phase3_spike/metadata.py:36-49.
        """
        with _METADATA_YML.open() as f:
            raw = yaml.safe_load(f)
        out: dict[str, dict] = {}
        for key, val in raw.items():
            stem = str(key).split(".")[0]  # "12.jpg" -> "12"
            out[stem] = val if isinstance(val, dict) else {"hits": [], "caliber": None}
        return out

    @staticmethod
    def primary_caliber_for(meta_entry: dict) -> str | None:
        """The primary caliber to inject as the UI-style hint.

        For single-caliber images: the caliber string. For mixed (list): the
        first. For missing: None.

        Ported from cv/phase3_spike/metadata.py:52-63.
        """
        cal = meta_entry.get("caliber")
        if isinstance(cal, list):
            return CaliberTaxonomy.normalize(cal[0]) if cal else None
        if isinstance(cal, str) and cal.strip():
            return CaliberTaxonomy.normalize(cal)
        return None

    @staticmethod
    def gt_hits_for(meta_entry: dict) -> list[int]:
        """Ground-truth score multiset for an image (sorted ascending).
        Ported from cv/phase3_spike/metadata.py:66-69.
        """
        hits = meta_entry.get("hits") or []
        return sorted(int(h) for h in hits)
