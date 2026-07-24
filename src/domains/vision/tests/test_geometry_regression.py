"""Numerical-identity regression gate for the geometry port.

Runs ``GeometryPipeline`` against the frozen metrics from cv/ (commit
76f6fc4, ``resources/train/intermediate_fused_all10/``). The rewrite must
copy the cv/ math verbatim — drift here means the port is wrong, not the
gate.

**Always runs on the 4 versioned fixtures** (ids 12, 46, 29, 21 —
byte-identical to ``resources/train/`` per the plan §57 default set, shipped
under ``tests/fixtures/``), so CI enforces the gate on every clone. When the
local 10-image train set (``resources/train/``) is also present, the
remaining 6 ids (1, 4, 6, 10, 19, 31) are appended for the full gate.

Run: ``uv run --group test pytest src/domains/vision/tests/test_geometry_regression.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.domains.vision.geometry.geometry_pipeline import GeometryPipeline
from src.domains.vision.tests.conftest import regression_image_set


# Frozen floats extracted from cv/approaches/full_pipeline/pipeline.py output
# (commit 76f6fc4) — see resources/train/intermediate_fused_all10/<id>_result.json.
# Tuple: (target_ring1_px, mean_ring_eccentricity, defense_layer).
FROZEN: dict[int, tuple[float, float, str]] = {
    1:   (393.8063357959023,   1.0185586637209985, "skip"),
    4:   (393.72882401539425,  1.0239054938643284, "lock_affine"),
    6:   (393.6101096243914,   1.0144244726008844, "skip"),
    10:  (393.65793452250966,  1.018243991259742,  "skip"),
    12:  (332.5134582809683,   1.0102748824774517, "skip"),
    19:  (393.48004007273056,  1.003900370145206,  "skip"),
    21:  (371.15423896645854,  1.0134673608914824, "skip"),
    29:  (393.8097903202544,   1.0092736268297622, "skip"),
    31:  (320.61007242336746,  1.0348329799870624, "lock_affine"),
    46:  (393.59221774821435,  1.0468494195445037, "lock_affine"),
}


# Resolve at collection time so pytest can parametrize on whatever is
# available — 4 ids in CI, 10 ids on a developer machine with resources/train/.
_REGRESSION_CASES: list[tuple[int, Path, Path]] = regression_image_set()


@pytest.mark.parametrize(
    "img_id, image_path, marked_path",
    _REGRESSION_CASES,
    ids=[str(cid) for cid, _, _ in _REGRESSION_CASES],
)
def test_geometry_pipeline_preserves_frozen_numerics(
    img_id: int,
    image_path: Path,
    marked_path: Path,
) -> None:
    pipeline = GeometryPipeline()
    result = pipeline.run(
        image_path,
        target_type="air_pistol",
        gt_marked_path=marked_path,
    )

    frozen_ring1, frozen_ecc, frozen_defense = FROZEN[img_id]

    # (a) invert err — the load-bearing identity gate (plan §2 contract).
    invert_err = result.coordinate_frame.self_test_inversion()
    assert invert_err < 1e-12, (
        f"img {img_id}: invert err {invert_err:.3e} >= 1e-12"
    )

    # (b) target_ring1_px — float-identical to cv/ (1e-9 tolerance).
    r1 = result.target_ring1_px
    assert abs(r1 - frozen_ring1) < 1e-9, (
        f"img {img_id}: target_ring1_px={r1!r} expected {frozen_ring1!r}"
    )

    # (c) mean_ring_eccentricity — float-identical to cv/ (1e-9 tolerance).
    ecc = result.metrics["mean_ring_eccentricity"]
    assert abs(ecc - frozen_ecc) < 1e-9, (
        f"img {img_id}: mean_ring_eccentricity={ecc!r} expected {frozen_ecc!r}"
    )

    # (d) defense-layer classification matches.
    layer = result.refinement.defense_layer
    assert layer == frozen_defense, (
        f"img {img_id}: defense_layer={layer!r} expected {frozen_defense!r}"
    )

