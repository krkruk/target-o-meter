"""Pytest fixtures for the vision domain test suite.

Two image sources coexist:

- **Versioned fixtures** under ``tests/fixtures/`` (4-image dataset: ids
  12, 46, 29, 21 + their ``_marked.jpg`` siblings). These ship with the repo
  and are used by every test that needs a real image (CLI smoke, pipeline
  runner, services). They guarantee CI reproducibility regardless of whether
  the developer has the local ``resources/train/`` set.

- **Local-only train images** under ``resources/train/`` (10-image dataset).
  These are NOT version-controlled (per project policy: ``resources/`` stays
  out of git). They back the geometry numerical-identity regression gate
  (``test_geometry_regression.py``), which needs all 10 train images to
  re-prove byte-identity with cv/'s frozen output. The gate skips gracefully
  when the local set is absent.
"""
from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
TRAIN_DIR = REPO_ROOT / "resources" / "train"

# The 4 versioned image ids (CLI default set). Shipped with the repo.
FIXTURE_IDS: list[int] = [12, 46, 29, 21]

# The 10-image train set used by the geometry regression gate. Local-only.
# Frozen r1@1024 + ecc + defense-layer from research § "Final per-image results"
# (commit 76f6fc4). Used by test_geometry_regression.py to re-prove byte-identity
# with cv/'s output.
TRAIN_IDS: list[int] = [1, 4, 6, 10, 12, 19, 21, 29, 31, 46]


# ---------------------------------------------------------------------------
# Versioned fixtures (always available)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Directory holding the 4 versioned test images + ``_marked.jpg`` siblings."""
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def fixture_images() -> dict[int, Path]:
    """The 4 versioned image paths keyed by id (12, 46, 29, 21)."""
    return {i: FIXTURES_DIR / f"{i}.jpg" for i in FIXTURE_IDS}


@pytest.fixture(scope="session")
def fixture_marked() -> dict[int, Path]:
    """The 4 versioned ``<id>_marked.jpg`` paths keyed by id."""
    return {i: FIXTURES_DIR / f"{i}_marked.jpg" for i in FIXTURE_IDS}


# ---------------------------------------------------------------------------
# Local-only train images (regression gate backing)
# ---------------------------------------------------------------------------

def has_local_train_set() -> bool:
    """True iff all 10 train images + their marked siblings are present locally."""
    if not TRAIN_DIR.exists():
        return False
    for i in TRAIN_IDS:
        if not (TRAIN_DIR / f"{i}.jpg").exists():
            return False
        if not (TRAIN_DIR / f"{i}_marked.jpg").exists():
            return False
    return True


@pytest.fixture(scope="session")
def train_dir() -> Path:
    """Directory holding the local 10-image train set (``resources/train/``)."""
    return TRAIN_DIR


@pytest.fixture(scope="session")
def train_images() -> list[Path]:
    """The 10 local train image paths (ids 1, 4, 6, 10, 12, 19, 21, 29, 31, 46).

    Requires ``resources/train/`` to exist locally; the regression test that
    uses this fixture skips when the set is absent.
    """
    return [TRAIN_DIR / f"{i}.jpg" for i in TRAIN_IDS]


@pytest.fixture(scope="session")
def marked_paths() -> dict[int, Path]:
    """Local ``<id>_marked.jpg`` paths — the GT-hole-extent overlay AdaptiveFrameSizer reads."""
    return {i: TRAIN_DIR / f"{i}_marked.jpg" for i in TRAIN_IDS}


@pytest.fixture(scope="session")
def frozen_ring1() -> dict[int, int]:
    """Frozen r1@1024 table — Phase 2 numerical-identity gate (rounded display values)."""
    return {
        1: 394, 4: 394, 6: 394, 10: 394, 12: 333,
        19: 394, 21: 371, 29: 394, 31: 321, 46: 394,
    }
