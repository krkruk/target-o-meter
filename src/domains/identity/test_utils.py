"""Data seeders and helpers for the identity domain.

Per AGENTS.md §5 (Test Encapsulation), system tests MUST NOT use ORM tools
directly against domain models; they go through test_utils.py or the REST API.
Mirrors the role of ``vision/test_utils.py``.
"""
from __future__ import annotations

import os

from src.domains.identity.models import User


def make_user(*, sub: str, nick: str | None = None, is_staff: bool = False) -> User:
    """Create a plain ``User`` row for tests.

    ``sub`` is required; ``nick`` defaults to the model's generated fallback
    when omitted (mirrors what the OAuth path does for a brand-new user).
    Role is *never* set here — derived from ``OWNER_SUB_ID`` on read.
    """
    return User.objects.create_user(sub=sub, nick=nick or "", is_staff=is_staff)


def make_owner(sub: str) -> User:
    """Create a row whose ``sub`` matches ``OWNER_SUB_ID`` in the test env.

    Owner is derived from ``self.sub == OWNER_SUB_ID`` (research §7), so making
    a row "the owner" means setting the env var to the given ``sub`` **then**
    creating the row. Callers must use ``monkeypatch.setenv`` to set the var so
    it tears down with the test.
    """
    os.environ["OWNER_SUB_ID"] = sub
    return User.objects.create_user(sub=sub, nick="test-owner")
