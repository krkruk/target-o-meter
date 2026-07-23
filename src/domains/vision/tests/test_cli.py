"""CLI smoke test — invokes ``__main__`` with ``--detector mock`` and asserts
the 3-file + ``_summary.json`` contract.

Passes a **path** (not an id) to the versioned fixture under
``tests/fixtures/12.jpg``. Confirms the CLI's image-path interface and its
``<stem>_marked.jpg`` sibling lookup both work.
"""
from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_cli_smoke_mock_detector(tmp_path: Path, monkeypatch) -> None:
    out_dir = tmp_path / "vision_out"
    image_path = FIXTURES / "12.jpg"
    monkeypatch.setattr(
        sys, "argv",
        [
            "python -m src.domains.vision",
            str(image_path),
            "--detector", "mock",
            "--out", str(out_dir),
            "--no-gt",
        ],
    )

    # The CLI exits with sys.exit(0) on success.
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("src.domains.vision", run_name="__main__")
    assert excinfo.value.code == 0

    # 3 deliverables + _summary.json written.
    assert (out_dir / "12_llm_input.png").exists()
    assert (out_dir / "12_marked.png").exists()
    assert (out_dir / "12_result.json").exists()
    assert (out_dir / "_summary.json").exists()

    summary = json.loads((out_dir / "_summary.json").read_text())
    assert summary["detector"] == "mock"
    assert summary["target_type"] == "air_pistol"
    assert summary["images"][0]["ok"] is True
    assert summary["images"][0]["count"] == 5
    # The summary records the absolute path the user passed in.
    assert summary["images"][0]["image"] == str(image_path)


def test_cli_resolves_marked_sibling_automatically(tmp_path: Path, monkeypatch) -> None:
    """When ``--no-gt`` is NOT passed, the CLI looks up ``<stem>_marked.jpg``
    in the same directory as the image and feeds it to AdaptiveFrameSizer."""
    out_dir = tmp_path / "vision_out_gt"
    image_path = FIXTURES / "12.jpg"
    monkeypatch.setattr(
        sys, "argv",
        [
            "python -m src.domains.vision",
            str(image_path),
            "--detector", "mock",
            "--out", str(out_dir),
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("src.domains.vision", run_name="__main__")
    assert excinfo.value.code == 0

    # GT-aware path produced a different target_ring1_px (img 12 enlarges
    # margin_factor to 1.54 with GT, shrinking r1 to ~333; non-GT gives ~394).
    result = json.loads((out_dir / "12_result.json").read_text())
    r1 = result["norm_meta"]["target_ring1_px"]
    assert 330 < r1 < 340, f"GT-aware r1 should be ~333, got {r1}"


def test_cli_skips_missing_path_and_continues(tmp_path: Path, monkeypatch) -> None:
    """A non-existent image path is reported and skipped; the rest of the run proceeds."""
    out_dir = tmp_path / "vision_out_skip"
    image_path = FIXTURES / "12.jpg"
    missing = tmp_path / "does_not_exist.jpg"
    monkeypatch.setattr(
        sys, "argv",
        [
            "python -m src.domains.vision",
            str(missing),
            str(image_path),
            "--detector", "mock",
            "--out", str(out_dir),
            "--no-gt",
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("src.domains.vision", run_name="__main__")
    assert excinfo.value.code == 0

    summary = json.loads((out_dir / "_summary.json").read_text())
    assert len(summary["images"]) == 2
    assert summary["images"][0]["ok"] is False
    assert summary["images"][1]["ok"] is True
    assert summary["images"][1]["count"] == 5
