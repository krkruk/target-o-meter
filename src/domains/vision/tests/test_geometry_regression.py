"""Numerical-identity regression gate for the geometry port.

Runs ``GeometryPipeline`` on all 10 train images and asserts the frozen
metrics from cv/ (commit 76f6fc4, ``resources/train/intermediate_fused_all10/``).
The rewrite must copy the cv/ math verbatim — drift here means the port is
wrong, not the gate.

**Local-only**: requires the unversioned ``resources/train/`` set (10 images
+ their ``_marked.jpg`` siblings, per project policy ``resources/`` is not in
git). The test skips gracefully when the local set is absent — CI runs the
4-image versioned fixture tests under ``tests/fixtures/`` instead.

Run: ``uv run --group test pytest src/domains/vision/tests/test_geometry_regression.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.domains.vision.geometry.geometry_pipeline import GeometryPipeline
from src.domains.vision.tests.conftest import has_local_train_set, TRAIN_IDS


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


_LOCAL_TRAIN_AVAILABLE = has_local_train_set()
_SKIP_REASON = (
    "requires the local 10-image train set at resources/train/ (not in git; "
    "ship the images manually before running this gate). The 4-image versioned "
    "fixture tests under tests/fixtures/ run regardless."
)


@pytest.mark.skipif(not _LOCAL_TRAIN_AVAILABLE, reason=_SKIP_REASON)
@pytest.mark.parametrize("img_id", TRAIN_IDS)
def test_geometry_pipeline_preserves_frozen_numerics(
    img_id: int,
    train_images: list[Path],
    marked_paths: dict[int, Path],
) -> None:
    image_path = next(p for p in train_images if p.stem == str(img_id))
    marked = marked_paths[img_id]

    pipeline = GeometryPipeline()
    result = pipeline.run(
        image_path,
        target_type="air_pistol",
        gt_marked_path=marked,
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

