"""Data seeders and helpers for the identity domain.

Per AGENTS.md §5 (Test Encapsulation), system tests MUST NOT use ORM tools
directly against domain models; they go through test_utils.py or the REST API.
Mirrors the role of ``vision/test_utils.py`` — pure row factories, no env
mutation (impl-review F3: env is owned by fixtures via ``monkeypatch.setenv``,
never by the seeder directly, so a stray ``make_owner(...)`` call without the
fixture can't leak ``OWNER_SUB_ID`` into sibling tests).
"""
from __future__ import annotations

from src.domains.identity.models import User


def make_user(*, sub: str, nick: str | None = None, is_staff: bool = False) -> User:
    """Create a plain ``User`` row for tests.

    ``sub`` is required; ``nick`` defaults to the model's generated fallback
    when omitted (mirrors what the OAuth path does for a brand-new user).
    Role is *never* set here — derived from ``OWNER_SUB_ID`` on read.
    """
    return User.objects.create_user(sub=sub, nick=nick or "", is_staff=is_staff)


def make_owner(sub: str) -> User:
    """Create a ``User`` row intended to be matched by ``OWNER_SUB_ID``.

    Pure row factory — does NOT touch ``OWNER_SUB_ID``. The caller is
    responsible for setting the env var via the ``owner_sub`` fixture (which
    uses ``monkeypatch.setenv`` so it tears down with the test). Owner is
    derived from ``self.sub == OWNER_SUB_ID`` on read (research §7), so
    "making an owner" is: set the env var to ``sub`` (fixture), then create
    the row (this function).
    """
    return User.objects.create_user(sub=sub, nick="test-owner")
