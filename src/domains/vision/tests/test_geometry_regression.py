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

Tolerances
----------
The pipeline is deterministic for a FIXED machine (numpy/scipy/opencv build +
CPU SIMD kernel + BLAS thread count) but is **not** bit-reproducible across
environments. Two effects make a float-identical ``1e-9`` gate unrealistic in
CI:

* **Multi-threaded OpenBLAS** reduces sums/dot-products in a non-associative
  order → non-determinism in the last 1–2 ULPs. (CI pins
  ``*_NUM_THREADS=1`` in the ``run-be-tests`` composite to minimise this, but
  it is not fully eliminated.)
* **CPU SIMD dispatch at load time**: the same bundled OpenBLAS wheel picks a
  different micro-kernel (SSE/AVX2/AVX-512) per host CPUID, so
  ``numpy.linalg.eigh`` / ``svd`` land on slightly different ULPs on
  ubuntu-latest vs a dev box.
* For the ``lock_affine`` images (ecc >= 1.02) these ULP differences run
  through ``scipy.optimize.least_squares`` and are then amplified ~1000x by
  the ``math.ceil(margin_factor * r_ring1)`` integer rounding in
  ``warp_projector.py`` → the ``target_ring1_px`` step size of ~0.01–0.02.

So the gate uses tolerances wide enough to absorb cross-environment ULP
amplification, but far tighter than any genuine algorithmic regression
(which would be O(1) or O(10), not O(0.02)). Do NOT tighten these back to
``1e-9`` — that pins the gate to a single machine and silently fails on every
other CPU/BLAS build (the CI-only red that motivated these values).
"""
from __future__ import annotations

import math
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
    # Kept tight: this is a true algebraic identity (round-trip through H),
    # not optimizer output, so it is bit-stable across environments.
    invert_err = result.coordinate_frame.self_test_inversion()
    assert invert_err < 1e-12, (
        f"img {img_id}: invert err {invert_err:.3e} >= 1e-12"
    )

    # (b) target_ring1_px — cross-environment tolerance (see module docstring).
    # Range ~320–394; worst observed CI drift ~0.02 (integer-rounding-amplified
    # optimizer ULPs on the lock_affine path). 0.05 gives ~2.5x margin over
    # the worst case while catching any real regression (O(1)/O(10)).
    r1 = result.target_ring1_px
    assert math.isclose(r1, frozen_ring1, abs_tol=0.05), (
        f"img {img_id}: target_ring1_px={r1!r} expected {frozen_ring1!r} "
        f"(diff {abs(r1 - frozen_ring1):.3e} >= 0.05)"
    )

    # (c) mean_ring_eccentricity — cross-environment tolerance (see module
    # docstring). Range ~1.00–1.05; closed-form metric off the same BLAS/CPU
    # variance.
    ecc = result.metrics["mean_ring_eccentricity"]
    assert math.isclose(ecc, frozen_ecc, abs_tol=1e-3), (
        f"img {img_id}: mean_ring_eccentricity={ecc!r} expected {frozen_ecc!r} "
        f"(diff {abs(ecc - frozen_ecc):.3e} >= 1e-3)"
    )

    # (d) defense-layer classification matches.
    # Categorical (skip/lock_affine/ecc_scaled), robust to float noise — kept
    # as exact equality.
    layer = result.refinement.defense_layer
    assert layer == frozen_defense, (
        f"img {img_id}: defense_layer={layer!r} expected {frozen_defense!r}"
    )

