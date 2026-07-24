"""Identity domain unit + integration tests.

Covers the load-bearing logic: the derived role (Owner match, empty-env
fail-closed, User default), nick CI-uniqueness, ``get_or_create_user_by_sub``
create/return-existing, and ``list_users`` no-``sub``.

Mirrors ``vision/tests/test_services_q2.py`` in structure (``pytestmark =
pytest.mark.django_db``, seeders via ``test_utils.py``).
"""
from __future__ import annotations

import pytest
from django.db import IntegrityError

from src.domains.identity.models import Role, User
from src.domains.identity.services import (
    get_or_create_user_by_sub,
    get_user_context,
    list_users,
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
    assert User.objects.filter(sub=user_sub).count() == 1, "second call must not create a duplicate"
    assert dto2.user_uuid == dto1.user_uuid


def test_get_user_context_raises_on_unknown_sub() -> None:
    """An unknown ``sub`` raises ``DoesNotExist`` (BFF maps to 401)."""
    with pytest.raises(User.DoesNotExist):
        get_user_context("auth0|never-seen")


# ---------------------------------------------------------------------------
# list_users
# ---------------------------------------------------------------------------

def test_list_users_returns_no_sub() -> None:
    """``list_users`` DTOs MUST omit ``sub`` (Zero Email Storage, Q1).

    The attribute itself must be absent — not merely falsy — so a Pydantic
    dump can never leak the OIDC subject to a client.
    """
    make_user(sub="auth0|x", nick="dave")
    make_user(sub="auth0|y", nick="erin")
    out = list_users()
    assert len(out) == 2
    for dto in out:
        assert not hasattr(dto, "sub"), "UserOut must not expose sub"
        assert set(dto.model_dump().keys()) == {"nick", "role"}
