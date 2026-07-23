"""CLI smoke test — invokes ``__main__`` with ``--detector mock`` and asserts
the 3-file + ``_summary.json`` contract.

Uses ``runpy.run_module`` so the CLI's ``if __name__ == "__main__"`` runs.
"""
from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest


def test_cli_smoke_mock_detector(tmp_path: Path, monkeypatch) -> None:
    out_dir = tmp_path / "vision_out"
    monkeypatch.setattr(
        sys, "argv",
        [
            "python -m src.domains.vision",
            "12",
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
