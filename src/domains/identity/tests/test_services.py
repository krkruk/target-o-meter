"""Identity domain unit + integration tests.

Covers the load-bearing logic: the derived role (Owner match, empty-env
fail-closed, User default), nick CI-uniqueness, ``get_or_create_user_by_sub``
create/return-existing.

Mirrors ``vision/tests/test_services_q2.py`` in structure (``pytestmark =
pytest.mark.django_db``, seeders via ``test_utils.py``).
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError

from src.domains.identity.models import Role, User
from src.domains.identity.services import (
    NickTakenError,
    get_or_create_user_by_sub,
    get_user_context,
    set_nick,
)
from src.domains.identity.test_utils import make_owner, make_user


pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Derived role
# ---------------------------------------------------------------------------


def test_role_owner_when_sub_matches_env(owner_sub: str) -> None:
    """``role`` returns OWNER iff ``self.sub == OWNER_SUB_ID``."""
    user = make_owner(owner_sub)
    assert user.role == Role.OWNER
    assert user.is_owner is True


def test_role_user_when_sub_does_not_match(owner_sub: str, user_sub: str) -> None:
    """A sub that isn't the configured owner's → USER."""
    user = make_user(sub=user_sub, nick="bob")
    assert user.role == Role.USER
    assert user.is_owner is False


def test_role_fails_closed_on_empty_owner_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty/missing ``OWNER_SUB_ID`` → never Owner (fails closed, research §7).

    This is the load-bearing safety property: a misconfigured prod env with no
    owner set must NOT accidentally confer Owner on anyone.
    """
    monkeypatch.delenv("OWNER_SUB_ID", raising=False)
    user = make_user(sub="auth0|anyone", nick="carol")
    assert user.role == Role.USER
    assert user.is_owner is False


# ---------------------------------------------------------------------------
# Nick CI-uniqueness
# ---------------------------------------------------------------------------


def test_nick_is_case_insensitive_unique() -> None:
    """``"Bob"`` then ``"bob"`` violates the CI-uniqueness constraint."""
    make_user(sub="auth0|a", nick="Bob")
    with pytest.raises(IntegrityError):
        make_user(sub="auth0|b", nick="bob")


# ---------------------------------------------------------------------------
# get_or_create_user_by_sub
# ---------------------------------------------------------------------------


def test_get_or_create_creates_then_returns_existing(user_sub: str) -> None:
    """First call creates; second returns the same row."""
    assert User.objects.filter(sub=user_sub).count() == 0

    dto1 = get_or_create_user_by_sub(user_sub)
    assert User.objects.filter(sub=user_sub).count() == 1
    assert dto1.sub == user_sub

    dto2 = get_or_create_user_by_sub(user_sub)
    assert User.objects.filter(sub=user_sub).count() == 1, (
        "second call must not create a duplicate"
    )
    assert dto2.user_uuid == dto1.user_uuid


def test_get_user_context_raises_on_unknown_sub() -> None:
    """An unknown ``sub`` raises ``DoesNotExist`` (BFF maps to 401)."""
    with pytest.raises(User.DoesNotExist):
        get_user_context("auth0|never-seen")


# ---------------------------------------------------------------------------
# has_set_nick — first-login signal (S-01)
# ---------------------------------------------------------------------------


def test_new_user_has_set_nick_defaults_false() -> None:
    """A brand-new user (OAuth create path) has ``has_set_nick=False`` so the
    SPA shows the first-login nick prompt."""
    user = make_user(sub="auth0|new", nick="shooter-deadbeef")
    assert user.has_set_nick is False


def test_has_set_nick_persists_when_written() -> None:
    """``has_set_nick`` is an ordinary writable column (the service flips it)."""
    user = make_user(sub="auth0|new", nick="shooter-deadbeef")
    user.has_set_nick = True
    user.save()
    refreshed = User.objects.get(sub=user.sub)
    assert refreshed.has_set_nick is True


# ---------------------------------------------------------------------------
# set_nick service (S-01)
# ---------------------------------------------------------------------------


def test_set_nick_updates_nick_and_flag() -> None:
    """Happy path: sets the nick, flips ``has_set_nick=True``, returns the DTO
    reflecting both."""
    user = make_user(sub="auth0|alice")
    dto = set_nick(user.sub, "alice")
    refreshed = User.objects.get(sub=user.sub)
    assert refreshed.nick == "alice"
    assert refreshed.has_set_nick is True
    assert dto.nick == "alice"
    assert dto.has_set_nick is True


def test_set_nick_trims_whitespace() -> None:
    """Whitespace-only padding is stripped before validation/persist."""
    user = make_user(sub="auth0|alice")
    dto = set_nick(user.sub, "  alice  ")
    assert dto.nick == "alice"


def test_set_nick_rejects_empty_after_trim() -> None:
    """A whitespace-only nick is rejected (1–64 chars after trim)."""
    user = make_user(sub="auth0|alice")
    with pytest.raises(ValueError):
        set_nick(user.sub, "   ")


def test_set_nick_rejects_too_long() -> None:
    """A nick longer than 64 chars is rejected (matches model column)."""
    user = make_user(sub="auth0|alice")
    with pytest.raises(ValueError):
        set_nick(user.sub, "x" * 65)


def test_set_nick_raises_nick_taken_on_ci_duplicate() -> None:
    """A nick that collides case-insensitively with an existing row is mapped
    from the DB IntegrityError to a domain ``NickTakenError`` the BFF can turn
    into a 409 — never an opaque IntegrityError leaking across the boundary."""
    make_user(sub="auth0|taken", nick="Bob")
    user = make_user(sub="auth0|alice")
    with pytest.raises(NickTakenError):
        set_nick(user.sub, "bob")  # CI collision with "Bob"


def test_set_nick_raises_does_not_exist_on_unknown_sub() -> None:
    """An unknown ``sub`` raises ``User.DoesNotExist`` (BFF maps to 401)."""
    with pytest.raises(User.DoesNotExist):
        set_nick("auth0|never-seen", "alice")
