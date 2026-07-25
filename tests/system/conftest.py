"""Shared fixtures for the system test suite.

System tests (AGENTS.md §4) exercise cross-domain API + integration behavior.
They go through ``test_utils.py`` or the REST API (AGENTS.md §5 — never ORM
tools like factory_boy directly against domain models).
"""
from __future__ import annotations

import pytest


@pytest.fixture
def owner_sub(monkeypatch: pytest.MonkeyPatch) -> str:
    """The canonical Owner sub for system tests. Pinned via monkeypatch so it
    tears down cleanly per-test."""
    sub = "auth0|sys-owner-sub"
    monkeypatch.setenv("OWNER_SUB_ID", sub)
    return sub


@pytest.fixture
def user_sub() -> str:
    """A plain (non-Owner) sub for system tests."""
    return "auth0|sys-user-sub"
