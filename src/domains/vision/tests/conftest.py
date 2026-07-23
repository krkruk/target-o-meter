"""Pytest fixtures for the vision domain regression suite.

Sets up the train-image + marked-path + frozen-metrics table fixtures Phase 2
needs, without coupling tests to ``cv/``. Run from the repo root via
``uv run pytest src/domains/vision/tests``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TRAIN_DIR = REPO_ROOT / "resources" / "train"

# Frozen r1@1024 table from research § "Final per-image results" — the
# numerical-identity gate for Phase 2. Match exact (no tolerance — the warp
# is deterministic and the rewrite copies the math verbatim).
FROZEN_RING1: dict[int, int] = {
    1: 394,
    4: 394,
    6: 394,
    10: 394,
    12: 333,
    19: 394,
    21: 371,
    29: 394,
    31: 321,
    46: 394,
}

TRAIN_IDS: list[int] = sorted(FROZEN_RING1.keys())


@pytest.fixture(scope="session")
def train_dir() -> Path:
    """Directory holding the 10 train images + their `<id>_marked.jpg` peers."""
    return TRAIN_DIR


@pytest.fixture(scope="session")
def train_images() -> list[Path]:
    """The 10 train image paths (ids 1, 4, 6, 10, 12, 19, 21, 29, 31, 46)."""
    return [TRAIN_DIR / f"{i}.jpg" for i in TRAIN_IDS]


@pytest.fixture(scope="session")
def marked_paths() -> dict[int, Path]:
    """``<id>_marked.jpg`` paths — the GT-hole-extent overlay AdaptiveFrameSizer reads."""
    return {i: TRAIN_DIR / f"{i}_marked.jpg" for i in TRAIN_IDS}


@pytest.fixture(scope="session")
def frozen_ring1() -> dict[int, int]:
    """Frozen r1@1024 table — Phase 2 numerical-identity gate."""
    return dict(FROZEN_RING1)
