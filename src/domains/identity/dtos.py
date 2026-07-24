"""Pydantic DTO contracts for the identity domain.

All data crossing the domain boundary (inter-domain communication and API
responses) is expressed here as Pydantic models (AGENTS.md §5 — DTOs only).
Mirrors ``vision/dtos.py``.

Zero Email Storage invariant (AGENTS.md §2): **no ``sub`` crosses out to the
client**. ``UserOut`` deliberately omits ``sub``; only ``UserContextDTO``
(internal seam) carries it.
"""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class UserContextDTO(BaseModel):
    """Internal seam DTO — what the BFF reads to know *who* is acting.

    Carries ``sub`` and ``user_uuid`` because the BFF needs both: ``sub`` to
    re-derive role (single source of truth) and ``user_uuid`` for cross-domain
    refs (AGENTS.md §5 — UUIDs, not FKs).
    """

    user_uuid: UUID
    sub: str
    nick: str
    role: str
    is_owner: bool


class UserOut(BaseModel):
    """Client-facing user projection — no ``sub`` (Zero Email Storage, Q1)."""

    nick: str
    role: str


class MeOut(BaseModel):
    """``/api/me`` response: auth-state bootstrap for the SPA.

    ``authenticated=False, user=None`` is unused on the wire (the route returns
    401 when the auth callable fails, never a 200 with this shape) — it exists
    only so the DTO is self-describing. The BFF returns the 200 path.
    """

    authenticated: bool
    user: UserOut | None = None
