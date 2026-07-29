"""S-04 ban-lifecycle + owner-list services.

Covers the ban create/lift/active-query lifecycle, the owner-guard, the
unknown-user guard, ``get_ban_status`` active-vs-expired-vs-prior, and the
paginated + searchable ``list_users_for_owner`` with bulk ban-status attach.

Mirrors ``test_services.py``: ``pytestmark = pytest.mark.django_db``, seeders
via ``test_utils.py`` (``make_user`` / ``make_owner`` / ``make_ban``).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from src.domains.identity.ban import Ban, Duration
from src.domains.identity.services import (
    CannotModifyOwnerError,
    NoActiveBanError,
    UserNotFoundError,
    ban_user,
    get_active_ban,
    get_ban_status,
    list_users_for_owner,
    unban_user,
)
from src.domains.identity.test_utils import make_ban, make_owner, make_user


pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# ban_user
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "duration, expected_delta",
    [
        (Duration.ONE_HOUR, timedelta(hours=1)),
        (Duration.ONE_DAY, timedelta(days=1)),
        (Duration.SEVEN_DAYS, timedelta(days=7)),
        (Duration.THIRTY_DAYS, timedelta(days=30)),
    ],
)
def test_ban_user_creates_ban_with_correct_banned_until(
    user_sub: str, duration: str, expected_delta: timedelta
) -> None:
    """``ban_user`` creates a ``Ban`` whose ``banned_until`` ≈ now + duration."""
    user = make_user(sub=user_sub, nick="alice")
    before = timezone.now()

    status = ban_user(user_sub=user.sub, duration=duration, reason="spamming the range")

    after = timezone.now()
    ban = Ban.objects.get(user=user)
    assert ban.reason == "spamming the range"
    assert ban.duration_kind == duration
    assert before + expected_delta <= ban.banned_until <= after + expected_delta
    # The returned DTO reflects the new active ban.
    assert status.is_banned is True
    assert status.reason == "spamming the range"
    assert status.banned_until == ban.banned_until
    assert status.has_prior_ban is True


def test_ban_user_refuses_the_owner(owner_sub: str) -> None:
    """Banning the owner is refused — ``CannotModifyOwnerError``."""
    owner = make_owner(owner_sub)
    with pytest.raises(CannotModifyOwnerError):
        ban_user(user_sub=owner.sub, duration=Duration.ONE_DAY, reason="trying to lock the owner out")


def test_ban_user_unknown_sub_raises(user_sub: str) -> None:
    """A sub with no row → ``UserNotFoundError`` (BFF maps to 404)."""
    with pytest.raises(UserNotFoundError):
        ban_user(user_sub="auth0|never-seen", duration=Duration.ONE_DAY, reason="no such user")


def test_ban_user_rejects_short_reason(user_sub: str) -> None:
    """A reason shorter than the min length is rejected (``ValueError`` → 422)."""
    user = make_user(sub=user_sub, nick="alice")
    with pytest.raises(ValueError):
        ban_user(user_sub=user.sub, duration=Duration.ONE_DAY, reason="no")


def test_ban_user_rejects_invalid_duration(user_sub: str) -> None:
    """A duration outside the four literals is rejected (``ValueError`` → 422)."""
    user = make_user(sub=user_sub, nick="alice")
    with pytest.raises(ValueError):
        ban_user(user_sub=user.sub, duration="2h", reason="not a valid duration")


# ---------------------------------------------------------------------------
# get_active_ban / get_ban_status — active vs expired vs prior
# ---------------------------------------------------------------------------


def test_get_active_ban_returns_active_ban(banned_user) -> None:
    """An active ban (future ``banned_until``, no ``lifted_at``) is returned."""
    ban = get_active_ban(banned_user.sub)
    assert ban is not None
    assert ban.user_id == banned_user.id
    assert ban.lifted_at is None


def test_get_active_ban_none_when_expired(user_sub: str) -> None:
    """A ban whose ``banned_until`` is past returns ``None`` (expired)."""
    user = make_user(sub=user_sub, nick="alice")
    make_ban(user=user, banned_until=timezone.now() - timedelta(days=1))
    assert get_active_ban(user.sub) is None


def test_get_active_ban_none_when_lifted(user_sub: str) -> None:
    """A ban with ``lifted_at`` set (unbanned early) is not active."""
    user = make_user(sub=user_sub, nick="alice")
    make_ban(
        user=user,
        banned_until=timezone.now() + timedelta(days=7),
        lifted_at=timezone.now(),
    )
    assert get_active_ban(user.sub) is None


def test_get_ban_status_active(banned_user) -> None:
    """Active ban → ``is_banned=True``, reason + expiry populated."""
    status = get_ban_status(banned_user.sub)
    assert status.is_banned is True
    assert status.reason == "fixture ban"
    assert status.banned_until is not None
    assert status.has_prior_ban is True


def test_get_ban_status_prior_but_not_active(user_sub: str) -> None:
    """An expired ban → ``is_banned=False``, ``has_prior_ban=True``."""
    user = make_user(sub=user_sub, nick="alice")
    make_ban(user=user, banned_until=timezone.now() - timedelta(days=1))
    status = get_ban_status(user.sub)
    assert status.is_banned is False
    assert status.has_prior_ban is True


def test_get_ban_status_no_history(user_sub: str) -> None:
    """A user with no bans → all-clear status."""
    user = make_user(sub=user_sub, nick="alice")
    status = get_ban_status(user.sub)
    assert status.is_banned is False
    assert status.has_prior_ban is False
    assert status.reason is None
    assert status.banned_until is None


# ---------------------------------------------------------------------------
# unban_user
# ---------------------------------------------------------------------------


def test_unban_user_sets_lifted_at(banned_user) -> None:
    """Unbanning sets ``lifted_at`` on the active ban; status reflects it."""
    status = unban_user(user_sub=banned_user.sub)
    assert status.is_banned is False
    assert status.has_prior_ban is True
    assert status.lifted_at is not None
    ban = Ban.objects.get(user=banned_user)
    assert ban.lifted_at is not None
    assert get_active_ban(banned_user.sub) is None


def test_unban_user_with_no_active_ban_raises(user_sub: str) -> None:
    """Unbanning when nothing is active → ``NoActiveBanError`` (409)."""
    user = make_user(sub=user_sub, nick="alice")
    with pytest.raises(NoActiveBanError):
        unban_user(user_sub=user.sub)


def test_unban_user_expired_ban_raises(user_sub: str) -> None:
    """An expired ban is not "active" — unban raises ``NoActiveBanError``."""
    user = make_user(sub=user_sub, nick="alice")
    make_ban(user=user, banned_until=timezone.now() - timedelta(days=1))
    with pytest.raises(NoActiveBanError):
        unban_user(user_sub=user.sub)


def test_unban_user_unknown_sub_raises(user_sub: str) -> None:
    with pytest.raises(UserNotFoundError):
        unban_user(user_sub="auth0|never-seen")


# ---------------------------------------------------------------------------
# list_users_for_owner — pagination, search, order, ban-status attach
# ---------------------------------------------------------------------------


def test_list_users_for_owner_paginates(owner_sub: str) -> None:
    """25 users → page 1 has 20 items, page 2 has the rest; ``total``/``total_pages``
    reflect the full set across both pages with no overlap."""
    make_owner(owner_sub)
    for i in range(25):
        make_user(sub=f"auth0|u{i}", nick=f"seed-{i:02d}")

    page1 = list_users_for_owner(page=1, page_size=20)
    page2 = list_users_for_owner(page=2, page_size=20)

    assert len(page1.items) == 20
    # Both pages together cover the full set (26) with no overlap.
    page1_subs = {u.sub for u in page1.items}
    page2_subs = {u.sub for u in page2.items}
    assert page1_subs.isdisjoint(page2_subs)
    assert len(page1_subs) + len(page2_subs) == 26
    assert page1.total == 26  # 25 seeded + owner
    assert page1.total_pages == 2
    assert page1.page == 1
    assert page1.page_size == 20


def test_list_users_for_owner_clamps_page_size(owner_sub: str) -> None:
    """``page_size`` > 50 is clamped to 50."""
    make_owner(owner_sub)
    for i in range(5):
        make_user(sub=f"auth0|u{i}", nick=f"user{i:02d}")
    out = list_users_for_owner(page=1, page_size=500)
    assert out.page_size == 50


def test_list_users_for_owner_filters_by_nick_ci(owner_sub: str) -> None:
    """``q`` filters by nick, case-insensitive substring."""
    make_owner(owner_sub)
    make_user(sub="auth0|a", nick="Alice")
    make_user(sub="auth0|b", nick="BOB")
    make_user(sub="auth0|c", nick="carol")

    out = list_users_for_owner(q="ali")
    nicks = {u.nick for u in out.items}
    assert nicks == {"Alice"}

    # "ar" matches carol only (not the owner's "test-owner", not BOB).
    out2 = list_users_for_owner(q="ar")
    assert {u.nick for u in out2.items} == {"carol"}


def test_list_users_for_owner_filters_by_sub(owner_sub: str) -> None:
    """``q`` also matches ``sub``."""
    make_owner(owner_sub)
    make_user(sub="auth0|special-token-123", nick="dave")
    make_user(sub="auth0|other", nick="erin")

    out = list_users_for_owner(q="special-token")
    assert {u.nick for u in out.items} == {"dave"}


def test_list_users_for_owner_orders_by_nick_asc(owner_sub: str) -> None:
    """Rows are ordered by nick ascending (case-insensitive)."""
    make_owner(owner_sub)
    make_user(sub="auth0|a", nick="zoe")
    make_user(sub="auth0|b", nick="Alice")
    make_user(sub="auth0|c", nick="bob")

    out = list_users_for_owner()
    nicks = [u.nick for u in out.items]
    # Case-insensitive ascending across the whole set.
    assert nicks == sorted(nicks, key=str.lower)


def test_list_users_for_owner_carries_sub(owner_sub: str) -> None:
    """The owner-list projection DOES carry ``sub`` (unlike ``UserOut``)."""
    make_owner(owner_sub)
    make_user(sub="auth0|visible", nick="alice")
    out = list_users_for_owner()
    alice = next(u for u in out.items if u.nick == "alice")
    assert alice.sub == "auth0|visible"


def test_list_users_for_owner_marks_owner_row(owner_sub: str) -> None:
    """The owner's own row is present with ``is_owner=True``."""
    make_owner(owner_sub)
    make_user(sub="auth0|plain", nick="alice")
    out = list_users_for_owner()
    owner_row = next(u for u in out.items if u.sub == owner_sub)
    assert owner_row.is_owner is True
    plain = next(u for u in out.items if u.sub == "auth0|plain")
    assert plain.is_owner is False


def test_list_users_for_owner_attaches_ban_status(owner_sub: str, user_sub: str) -> None:
    """Active + prior ban states are attached to the right rows."""
    make_owner(owner_sub)
    active = make_user(sub=user_sub, nick="alice")
    make_ban(user=active, banned_until=timezone.now() + timedelta(days=7), reason="active ban")

    prior = make_user(sub="auth0|prior", nick="bob")
    make_ban(user=prior, banned_until=timezone.now() - timedelta(days=1))  # expired

    make_user(sub="auth0|clean", nick="carol")

    out = list_users_for_owner()
    by_nick = {u.nick: u for u in out.items}
    assert by_nick["alice"].ban.is_banned is True
    assert by_nick["alice"].ban.has_prior_ban is True
    assert by_nick["alice"].ban.reason == "active ban"
    assert by_nick["bob"].ban.is_banned is False
    assert by_nick["bob"].ban.has_prior_ban is True
    assert by_nick["carol"].ban.is_banned is False
    assert by_nick["carol"].ban.has_prior_ban is False


def test_list_users_for_owner_empty(owner_sub: str) -> None:
    """No users at all → empty items, zero total."""
    out = list_users_for_owner()
    assert out.items == []
    assert out.total == 0
    assert out.total_pages == 0
