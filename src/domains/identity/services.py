"""Pure business logic for the identity domain.

Per AGENTS.md §5, this module MUST NOT import django-ninja or handle HTTP.
Only src/bff/ is permitted to do so. These services take primitives, return
DTOs — the BFF calls them; nothing imports ORM objects across the boundary.

Mirrors ``vision/services.py`` in shape (pure functions over the ORM, DTOs at
the seam).
"""

from __future__ import annotations

from django.db import IntegrityError
from django.db.models import Count, Q
from django.db.models.functions import Lower
from django.utils import timezone

from src.domains.identity.ban import Ban, _DURATION_DELTAS
from src.domains.identity.dtos import (
    AdminUserListOut,
    AdminUserOut,
    BanStatusOut,
    UserContextDTO,
    UserOut,
)
from src.domains.identity.models import User, _generated_nick


class NickTakenError(Exception):
    """Domain signal that a nick is already in use (CI-collision).

    Raised by ``set_nick`` after catching the ``identity_user_nick_ci_unique``
    ``IntegrityError``. The BFF maps this to ``HttpError(409)``. Keeping the DB
    error inside the domain (and surfacing a typed domain exception at the
    seam) is the contract from AGENTS.md §5 — no ORM/IntegrityError crosses the
    boundary.
    """


# ---------------------------------------------------------------------------
# S-04 typed domain exceptions. Mirror ``NickTakenError`` — the BFF maps each
# to a specific HTTP status (404 / 409 / 422). No ORM/IntegrityError crosses.
# ---------------------------------------------------------------------------


class UserNotFoundError(Exception):
    """A referenced ``sub`` has no row (BFF → 404)."""


class CannotModifyOwnerError(Exception):
    """The target is the owner — ban/delete refused (BFF → 409)."""


class NoActiveBanError(Exception):
    """``unban_user`` called with no active ban (BFF → 409)."""


# 1–64 chars after trim — matches ``User.nick``'s ``max_length=64``.
_NICK_MIN_LEN = 1
_NICK_MAX_LEN = 64


def _user_to_context_dto(user: User) -> UserContextDTO:
    """Map a ``User`` row → ``UserContextDTO`` (the internal seam DTO)."""
    return UserContextDTO(
        user_uuid=user.id,
        sub=user.sub,
        nick=user.nick,
        role=user.role,
        is_owner=user.is_owner,
        has_set_nick=user.has_set_nick,
    )


def get_or_create_user_row(sub: str) -> tuple[User, bool]:
    """Resolve-or-create the ``User`` row; return ``(row, is_first_login_ever)``.

    Called by the BFF callback after Auth0 has proved identity. Returns the
    ORM row (``django.contrib.auth.login`` is keyed on the model instance,
    not a DTO) and an ``is_first_login_ever`` flag (True when no users
    existed before this call) — used by the callback's owner-bootstrap
    logging.

    This is the one service that returns an ORM object rather than a DTO.
    Everywhere else the seam stays DTO-only (AGENTS.md §5); ``login()``'s
    model-instance requirement is the legitimate, isolated exception.

    ``is_first_login_ever`` is checked *before* the upsert so a returning
    user (the only user, re-logging-in) correctly reads False — checking
    ``exclude(sub=sub)`` after the upsert would re-fire on every same-user
    relogin. Role is never set here: it is *derived* from ``OWNER_SUB_ID``
    on read.
    """
    is_first_login_ever = not User.objects.exists()
    user, _created = User.objects.get_or_create(
        sub=sub,
        defaults={"nick": _generated_nick()},
    )
    return user, is_first_login_ever


def get_or_create_user_by_sub(sub: str) -> UserContextDTO:
    """Resolve-or-create the ``User`` row for an Auth0 ``sub`` (DTO seam).

    Thin DTO wrapper over ``get_or_create_user_row`` for callers that don't
    need the ORM row (e.g. ``login()``-free paths). New rows get a generated
    nick (F-01 fallback — S-01 adds the nick-on-first-login prompt).
    """
    user, _is_first_login_ever = get_or_create_user_row(sub)
    return _user_to_context_dto(user)


def get_user_context(sub: str) -> UserContextDTO:
    """Read accessor — fetch by ``sub``, return the DTO.

    Mirrors the ``vision/services.get_job`` read pattern. Raises
    ``User.DoesNotExist`` if absent; the BFF maps that to 401 (an unknown sub
    means no session should be valid).
    """
    user = User.objects.get(sub=sub)
    return _user_to_context_dto(user)


def is_owner(dto: UserContextDTO) -> bool:
    """Thin read of ``dto.is_owner`` — exists so the BFF's ``require_owner``
    dependency expresses intent in domain terms, not by reaching into DTO
    fields. (``is_owner`` is itself a derived property on ``User``.)"""
    return dto.is_owner


def list_users() -> list[UserOut]:
    """Return all users as ``UserOut`` DTOs (no ``sub``).

    Backs the demo owner route (Phase 3.5). Returns an empty list until S-04
    adds real data — but the mapping surface is proven now.
    """
    return [
        UserOut(nick=u.nick, role=u.role, has_set_nick=u.has_set_nick)
        for u in User.objects.all()
    ]


# ---------------------------------------------------------------------------
# S-04: ban lifecycle + owner-list services.
#
# Primitives in, DTOs out; typed exceptions; no ORM/IntegrityError crossing
# the seam. ``get_active_ban`` is the one ORM-returning read for the OAuth
# callback's convenience — documented as such, mirroring
# ``get_or_create_user_row``.
# ---------------------------------------------------------------------------

_REASON_MIN_LEN = 5
_REASON_MAX_LEN = 500
_DEFAULT_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 50


def _ban_to_status(ban: Ban | None, has_prior: bool) -> BanStatusOut:
    """Map a ``Ban`` (or None) + a prior-flag into the ``BanStatusOut`` DTO.

    ``is_banned`` is re-derived here (not assumed by the caller) so this helper
    is correct for both active and historical bans: a ban with ``lifted_at``
    set or ``banned_until`` in the past reads ``is_banned=False`` but still
    surfaces its ``reason``/``banned_until``/``lifted_at`` (so an unbanned user
    keeps the detail the UI may show, and the unban response carries
    ``lifted_at``).
    """
    if ban is None:
        return BanStatusOut(
            is_banned=False,
            reason=None,
            banned_until=None,
            lifted_at=None,
            has_prior_ban=has_prior,
        )
    is_active = ban.lifted_at is None and ban.banned_until > timezone.now()
    return BanStatusOut(
        is_banned=is_active,
        reason=ban.reason,
        banned_until=ban.banned_until,
        lifted_at=ban.lifted_at,
        has_prior_ban=has_prior,
    )


def get_active_ban(user_sub: str) -> Ban | None:
    """Return the active ``Ban`` for ``user_sub`` (or ``None``).

    "Active" = ``banned_until > now() AND lifted_at IS NULL``. The single
    ORM-returning read accessor — used by the OAuth callback's enforcement
    check. Mirrors the ``get_or_create_user_row`` exception pattern
    (services.py) where ``login()``'s model-instance requirement justifies an
    ORM return at an isolated, documented seam. Here the callback needs the
    row's ``reason``/``banned_until`` to render the banned page.
    """
    return (
        Ban.objects.filter(
            user__sub=user_sub,
            lifted_at__isnull=True,
            banned_until__gt=timezone.now(),
        )
        .order_by("-banned_until")
        .first()
    )


def get_ban_status(user_sub: str) -> BanStatusOut:
    """Build the per-user ban status the admin list + callback use.

    Returns a DTO (clean boundary): the active ban (if any) plus a prior flag
    (true if ANY ban exists, active or expired). The callback reads
    ``is_banned`` to decide whether to block the login. When no ban is active
    but history exists, the most recent ban's detail is surfaced (so an unbanned
    user keeps ``lifted_at``/``reason`` for the UI + the unban response).
    """
    has_prior = Ban.objects.filter(user__sub=user_sub).exists()
    latest = (
        Ban.objects.filter(user__sub=user_sub).order_by("-banned_at").first()
        if has_prior
        else None
    )
    return _ban_to_status(latest, has_prior)


def ban_user(*, user_sub: str, duration: str, reason: str) -> BanStatusOut:
    """Create a ``Ban`` row for ``user_sub``; return the new active status.

    - Unknown sub → ``UserNotFoundError`` (BFF → 404).
    - Target is the owner → ``CannotModifyOwnerError`` (BFF → 409).
    - ``duration`` not one of the four literals → ``ValueError`` (BFF → 422).
    - ``reason`` shorter than 5 chars (after trim) → ``ValueError`` (BFF → 422).
      The DTO enforces this too; the service double-checks defensively.
    """
    try:
        user = User.objects.get(sub=user_sub)
    except User.DoesNotExist:
        raise UserNotFoundError(user_sub) from None
    if user.is_owner:
        raise CannotModifyOwnerError(user_sub)

    reason = (reason or "").strip()
    if not (_REASON_MIN_LEN <= len(reason) <= _REASON_MAX_LEN):
        raise ValueError(
            f"reason must be {_REASON_MIN_LEN}–{_REASON_MAX_LEN} chars after trim"
        )
    delta = _DURATION_DELTAS.get(duration)
    if delta is None:
        raise ValueError(f"invalid duration: {duration!r}")

    banned_until = timezone.now() + delta
    Ban.objects.create(
        user=user, reason=reason, duration_kind=duration, banned_until=banned_until
    )
    return get_ban_status(user_sub)


def unban_user(*, user_sub: str) -> BanStatusOut:
    """Lift the active ban on ``user_sub`` (set ``lifted_at``); return status.

    - Unknown sub → ``UserNotFoundError`` (BFF → 404).
    - No active ban → ``NoActiveBanError`` (BFF → 409).
    """
    if not User.objects.filter(sub=user_sub).exists():
        raise UserNotFoundError(user_sub) from None
    ban = get_active_ban(user_sub)
    if ban is None:
        raise NoActiveBanError(user_sub)
    ban.lifted_at = timezone.now()
    ban.save(update_fields=["lifted_at"])
    return get_ban_status(user_sub)


def delete_user(*, user_sub: str) -> None:
    """Hard-delete the ``User`` row for ``user_sub`` (cascades to ``Ban`` rows).

    - Unknown sub → ``UserNotFoundError`` (BFF → 404).
    - Target is the owner → ``CannotModifyOwnerError`` (BFF → 409).
    """
    try:
        user = User.objects.get(sub=user_sub)
    except User.DoesNotExist:
        raise UserNotFoundError(user_sub) from None
    if user.is_owner:
        raise CannotModifyOwnerError(user_sub)
    user.delete()


def list_users_for_owner(
    *, q: str = "", page: int = 1, page_size: int = _DEFAULT_PAGE_SIZE
) -> AdminUserListOut:
    """Owner-only paginated + searchable list of users with ban status.

    - ``q`` filters by nick or sub (case-insensitive substring).
    - Ordered by nick ascending (case-insensitive).
    - Offset pagination (hobbyist scale); ``page_size`` clamped to ≤50,
      ``page`` clamped to ≥1.
    - Bulk-fetches active bans + prior-ban existence for the page's users
      (two queries, no N+1).

    The owner's own row is included with ``is_owner=True`` so the SPA can hide
    ban/delete buttons on it.
    """
    page = max(1, page)
    page_size = max(1, min(page_size, _MAX_PAGE_SIZE))

    qs = User.objects.all()
    q_trimmed = (q or "").strip()
    if q_trimmed:
        qs = qs.filter(Q(nick__icontains=q_trimmed) | Q(sub__icontains=q_trimmed))
    qs = qs.order_by(Lower("nick"))

    total = qs.count()
    total_pages = (total + page_size - 1) // page_size if total else 0
    offset = (page - 1) * page_size
    page_users = list(qs[offset : offset + page_size])

    if not page_users:
        return AdminUserListOut(
            items=[], page=page, page_size=page_size, total=total, total_pages=total_pages
        )

    # Bulk fetch: active bans + prior-ban existence for the page's users.
    now = timezone.now()
    page_user_ids = [u.id for u in page_users]
    active_bans = {
        b.user_id: b
        for b in Ban.objects.filter(
            user_id__in=page_user_ids, lifted_at__isnull=True, banned_until__gt=now
        )
    }
    prior_counts = {
        row["user"]: row["count"]
        for row in Ban.objects.filter(user_id__in=page_user_ids)
        .values("user")
        .annotate(count=Count("id"))
    }

    items = [
        AdminUserOut(
            user_uuid=u.id,
            sub=u.sub,
            nick=u.nick,
            has_set_nick=u.has_set_nick,
            is_owner=u.is_owner,
            ban=_ban_to_status(
                active_bans.get(u.id), prior_counts.get(u.id, 0) > 0
            ),
        )
        for u in page_users
    ]
    return AdminUserListOut(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )


def set_nick(sub: str, nick: str) -> UserContextDTO:
    """Set the calling user's nick and mark first-login complete.

    The SPA's first-login prompt calls this via ``PATCH /v1/me``. Pure business
    logic — takes primitives, returns the refreshed DTO. Validation +
    uniqueness are enforced here so the BFF route stays a thin wrapper
    (AGENTS.md §5 — no HTTP in domains, DTOs at the seam).

    - ``sub`` resolves the row; unknown → ``User.DoesNotExist`` (BFF → 401).
    - ``nick`` is trimmed; rejected if empty or longer than 64 chars after
      trim (``ValueError`` — the BFF turns this into a 422 via the route's
      request-schema validation, which we mirror here as a defensive guard).
    - The ``identity_user_nick_ci_unique`` constraint is the uniqueness backstop;
      its ``IntegrityError`` is mapped to ``NickTakenError`` (BFF → 409) so no
      DB exception crosses the boundary.
    - On success ``has_set_nick`` flips to ``True`` (the SPA's prompt-gate).
    """
    nick = (nick or "").strip()
    if not (_NICK_MIN_LEN <= len(nick) <= _NICK_MAX_LEN):
        raise ValueError(
            f"nick must be {_NICK_MIN_LEN}–{_NICK_MAX_LEN} chars after trim"
        )

    user = User.objects.get(sub=sub)
    user.nick = nick
    user.has_set_nick = True
    try:
        user.save(update_fields=["nick", "has_set_nick"])
    except IntegrityError as exc:
        # The CI-uniqueness constraint fired — translate to the domain signal.
        raise NickTakenError(nick) from exc
    return _user_to_context_dto(user)
