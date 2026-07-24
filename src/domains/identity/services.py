"""Pure business logic for the identity domain.

Per AGENTS.md §5, this module MUST NOT import django-ninja or handle HTTP.
Only src/bff/ is permitted to do so. These services take primitives, return
DTOs — the BFF calls them; nothing imports ORM objects across the boundary.

Mirrors ``vision/services.py`` in shape (pure functions over the ORM, DTOs at
the seam).
"""
from __future__ import annotations

from src.domains.identity.dtos import UserContextDTO, UserOut
from src.domains.identity.models import User, _generated_nick


def _user_to_context_dto(user: User) -> UserContextDTO:
    """Map a ``User`` row → ``UserContextDTO`` (the internal seam DTO)."""
    return UserContextDTO(
        user_uuid=user.id,
        sub=user.sub,
        nick=user.nick,
        role=user.role,
        is_owner=user.is_owner,
    )


def get_or_create_user_by_sub(sub: str) -> UserContextDTO:
    """Resolve-or-create the ``User`` row for an Auth0 ``sub``.

    Called by the BFF callback after Auth0 has proved identity. New rows get a
    generated nick (F-01 fallback — S-01 adds the nick-on-first-login prompt).
    Role is never set here: it is *derived* from ``OWNER_SUB_ID`` on read.
    """
    user, _created = User.objects.get_or_create(
        sub=sub,
        defaults={"nick": _generated_nick()},
    )
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
    return [UserOut(nick=u.nick, role=u.role) for u in User.objects.all()]
