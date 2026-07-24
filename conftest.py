"""Root pytest configuration (F-01 Phase 6.2).

Registers the ``dev`` / ``uat`` markers (belt-and-suspenders alongside
pyproject's ``markers`` list) and provides the autouse UAT-skip: even
``pytest -m uat`` skips every UAT test unless ``RUN_UAT=1`` is set. This makes
a missing-secret or accidental run a skip, not a red build.

The UAT test itself is deferred to a later slice (Q7); this scaffolding is what
that future test will consume.
"""
from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    """Register markers (in addition to pyproject's list)."""
    config.addinivalue_line(
        "markers",
        "dev: fast, Auth0-bypassed tests (default).",
    )
    config.addinivalue_line(
        "markers",
        "uat: slow acceptance tests hitting REAL Auth0; skipped unless RUN_UAT=1.",
    )


@pytest.fixture(autouse=True)
def _skip_uat_unless_opted_in(request):
    """Belt-and-suspenders: skip any ``uat``-marked test unless RUN_UAT=1.

    pyproject's ``addopts = -m "not uat"`` already excludes UAT from the
    default run. This fixture covers the explicit ``pytest -m uat`` case: even
    then, the test is skipped unless the env var is set. A missing Auth0 secret
    must never turn the build red.
    """
    if request.node.get_closest_marker("uat") and not os.environ.get("RUN_UAT"):
        pytest.skip("UAT skipped: set RUN_UAT=1 (requires real Auth0 creds).")
