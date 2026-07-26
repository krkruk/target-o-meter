"""Pure business logic for the identity domain.

Per AGENTS.md §5, this module MUST NOT import django-ninja or handle HTTP.
Only src/bff/ is permitted to do so. These services take primitives, return
DTOs — the BFF calls them; nothing imports ORM objects across the boundary.

Mirrors ``vision/services.py`` in shape (pure functions over the ORM, DTOs at
the seam).
"""

from __future__ import annotations

from django.db import IntegrityError

from src.domains.identity.dtos import UserContextDTO, UserOut
from src.domains.identity.models import User, _generated_nick


class NickTakenError(Exception):
    """Domain signal that a nick is already in use (CI-collision).

    Raised by ``set_nick`` after catching the ``identity_user_nick_ci_unique``
    ``IntegrityError``. The BFF maps this to ``HttpError(409)``. Keeping the DB
    error inside the domain (and surfacing a typed domain exception at the
    seam) is the contract from AGENTS.md §5 — no ORM/IntegrityError crosses the
    boundary.
    """


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
