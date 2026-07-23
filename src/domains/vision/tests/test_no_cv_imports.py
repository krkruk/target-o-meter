"""No-cv-import guardrail — a CI test that the invariant cannot regress silently.

Parses the domain source tree (``src/domains/vision/**/*.py``) with the
``ast`` module and asserts no module-level import starts with the ``cv``
package (the research sandbox at commit 76f6fc4). ``cv2`` (opencv) is a
separate package and is allowed.

Per plan §7 §1: ``test_no_runtime_cv_imports()`` walks
``src/domains/vision/**/*.py``, parses imports, asserts none start with
``cv``. Excludes ``tests/`` fixtures if any deliberately reference cv/ for
comparison (none expected).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
VISION_PKG = REPO_ROOT / "src" / "domains" / "vision"


def _imported_modules(tree: ast.AST) -> list[str]:
    """All module names imported at module level (``import x`` or ``from x``)."""
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                out.append(node.module)
    return out


def _walk_py_files(root: Path):
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def test_no_runtime_cv_imports() -> None:
    """Every module under ``src/domains/vision/`` is free of imports of the
    ``cv`` package (the research sandbox).

    ``cv2`` (opencv-python-headless) is a different package and is allowed.
    """
    offenders: list[tuple[Path, str]] = []
    for path in _walk_py_files(VISION_PKG):
        tree = ast.parse(path.read_text(), filename=str(path))
        for mod in _imported_modules(tree):
            # Match the `cv` package or any submodule (cv.blob_detect, cv.approaches).
            # cv2 is opencv-python-headless — a separate package and allowed.
            if mod == "cv" or mod.startswith("cv."):
                offenders.append((path, mod))

    assert not offenders, (
        f"vision domain must not depend on the cv/ research sandbox. "
        f"Found {len(offenders)} cv-import(s):\n"
        + "\n".join(f"  {p}: {mod}" for p, mod in offenders)
    )


def test_no_runtime_cv_imports_in_tests() -> None:
    """Tests must not depend on the cv/ sandbox either (no comparison fixtures
    reference cv/ — none expected per plan §7 §1).
    """
    offenders: list[tuple[Path, str]] = []
    tests_dir = VISION_PKG / "tests"
    if not tests_dir.exists():
        pytest.skip("no tests directory")
    for path in _walk_py_files(tests_dir):
        tree = ast.parse(path.read_text(), filename=str(path))
        for mod in _imported_modules(tree):
            if mod == "cv" or mod.startswith("cv."):
                offenders.append((path, mod))

    assert not offenders, (
        f"vision tests must not import cv/. Found {len(offenders)}:\n"
        + "\n".join(f"  {p}: {mod}" for p, mod in offenders)
    )
