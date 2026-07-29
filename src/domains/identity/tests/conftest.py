"""Pytest fixtures for the identity domain test suite.

Per AGENTS.md §5, system tests go through ``test_utils.py`` (no ORM tools
directly). These fixtures wrap the seeders for the unit tests.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from src.domains.identity.test_utils import make_ban, make_user


@pytest.fixture
def owner_sub(monkeypatch: pytest.MonkeyPatch) -> str:
    """The ``sub`` value that ``OWNER_SUB_ID`` is pinned to for the test.

    Centralized so every test deriving role starts from the same env state;
    ``monkeypatch`` tears it down. Use ``make_owner(owner_sub)`` to seed the row.
    """
    sub = "auth0|test-owner-sub"
    monkeypatch.setenv("OWNER_SUB_ID", sub)
    return sub


@pytest.fixture
def user_sub() -> str:
    """A plain (non-Owner) ``sub`` for tests that need a User row."""
    return "auth0|test-user-sub"


@pytest.fixture
def seeded_user(user_sub: str) -> "object":  # type: ignore[name-defined]
    """A plain ``User`` row (NOT the owner)."""
    return make_user(sub=user_sub, nick="alice")


@pytest.fixture
def banned_user(user_sub: str) -> "object":  # type: ignore[name-defined]
    """A plain ``User`` with an ACTIVE ``Ban`` (far-future ``banned_until``).

    The ban is created directly via ``make_ban`` (not the service) so tests of
    ``get_active_ban``/``get_ban_status`` start from a known state without
    exercising the create path.
    """
    user = make_user(sub=user_sub, nick="banned-alice")
    make_ban(
        user=user,
        reason="fixture ban",
        banned_until=timezone.now() + timedelta(days=7),
    )
    return user
