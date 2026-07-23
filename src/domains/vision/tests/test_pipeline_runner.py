"""``PipelineRunner`` end-to-end integration test (mock detector, no network).

Runs the full pipeline (geometry + MockDetector + renderer) on img 12 (from
the versioned ``tests/fixtures/`` set) and asserts the 3-file deliverable
contract.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.domains.vision.detectors.mock_detector import MockDetector
from src.domains.vision.pipeline.pipeline_runner import PipelineRunner


FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def img_12_path() -> Path:
    return FIXTURES / "12.jpg"


@pytest.fixture(scope="module")
def img_12_marked() -> Path:
    return FIXTURES / "12_marked.jpg"


def test_pipeline_runner_writes_three_deliverables(
    img_12_path: Path,
    img_12_marked: Path,
    tmp_path: Path,
) -> None:
    runner = PipelineRunner(MockDetector())
    result = runner.run(
        img_12_path,
        target_type="air_pistol",
        caliber_hint="9mm",
        out_dir=tmp_path,
        gt_marked_path=img_12_marked,
    )

    # 3 deliverables written.
    assert (tmp_path / "12_llm_input.png").exists()
    assert (tmp_path / "12_marked.png").exists()
    assert (tmp_path / "12_result.json").exists()

    # _result.json parses.
    parsed = json.loads((tmp_path / "12_result.json").read_text())
    assert parsed["ok"] is True
    assert parsed["count"] == 5
    assert parsed["detector"] == "mock"
    assert parsed["target_type"] == "air_pistol"
    assert parsed["caliber_hint"] == "9mm"
    assert parsed["approach"] == "vision_pipeline"
    # Classical scores computed alongside the LLM (mock) scores.
    assert len(parsed["scores_classical"]) == 5
    # Mock detector returns [10,7,7,7,7] → total_llm = 38
    assert parsed["total_llm"] == 38
    # self_test passed (invert err is tiny for img 12 — frozen table reports 1.1e-13)
    assert parsed["self_test"]["passed"] is True

    # _marked.png is a non-empty image file.
    marked_size = (tmp_path / "12_marked.png").stat().st_size
    assert marked_size > 1000, f"_marked.png too small ({marked_size} bytes)"

    # llm_input.png is also non-empty.
    llm_input_size = (tmp_path / "12_llm_input.png").stat().st_size
    assert llm_input_size > 1000

    # Returned dict matches the JSON shape.
    assert result["count"] == parsed["count"]
    assert result["detector"] == parsed["detector"]


def test_pipeline_runner_skips_files_when_no_out_dir(
    img_12_path: Path,
    img_12_marked: Path,
    tmp_path: Path,
) -> None:
    """When ``out_dir`` is None, no files are written; the result dict still returns."""
    runner = PipelineRunner(MockDetector())
    result = runner.run(
        img_12_path,
        target_type="air_pistol",
        gt_marked_path=img_12_marked,
    )
    assert result["count"] == 5
    assert not any(tmp_path.iterdir())
