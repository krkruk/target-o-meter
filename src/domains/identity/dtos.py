"""Pydantic DTO contracts for the identity domain.

All data crossing the domain boundary (inter-domain communication and API
responses) is expressed here as Pydantic models (AGENTS.md §5 — DTOs only).
Mirrors ``vision/dtos.py``.

Zero Email Storage invariant (AGENTS.md §2): **no ``sub`` crosses out to the
client**. ``UserOut`` deliberately omits ``sub``; only ``UserContextDTO``
(internal seam) carries it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


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
    # S-01: surfaced to the SPA via ``MeOut.has_set_nick`` so the client can
    # gate the first-login nick prompt on an explicit flag rather than a
    # fragile ``shooter-*`` string-match.
    has_set_nick: bool


class UserOut(BaseModel):
    """Client-facing user projection — no ``sub`` (Zero Email Storage, Q1)."""

    nick: str
    role: str
    # S-01: the SPA gates the first-login nick prompt on this flag.
    has_set_nick: bool


class MeOut(BaseModel):
    """``/v1/me`` response: auth-state bootstrap for the SPA.

    ``authenticated=False, user=None`` is unused on the wire (the route returns
    401 when the auth callable fails, never a 200 with this shape) — it exists
    only so the DTO is self-describing. The BFF returns the 200 path.
    """

    authenticated: bool
    user: UserOut | None = None


class NickIn(BaseModel):
    """``PATCH /v1/me`` request body — the first-login nick prompt payload.

    Length bounds mirror the model column (1–64 after trim); the service trims
    and re-validates defensively. django-ninja turns a schema violation into
    422 automatically.
    """

    nick: str = Field(min_length=1, max_length=64)


class ErrorOut(BaseModel):
    """Generic error envelope for declared non-2xx responses (e.g. 409)."""

    detail: str


# ---------------------------------------------------------------------------
# S-04: owner-admin DTOs.
#
# ``UserOut`` (above) stays sub-less for ``/v1/me`` (Zero Email Storage holds
# for the user-facing audience). ``AdminUserOut`` is a DIFFERENT audience — the
# owner needs ``sub`` to identify users across nick changes and to match rows
# against the Auth0 dashboard. So it is a separate DTO, not a relaxation of
# ``UserOut``.
# ---------------------------------------------------------------------------


class BanStatusOut(BaseModel):
    """The per-user ban status attached to every admin-list row.

    ``is_banned`` is true iff an active ban exists (future ``banned_until``,
    ``lifted_at`` null). ``has_prior_ban`` is true if ANY ban (active or
    expired) exists — drives the grey "Banned before" chip.
    """

    is_banned: bool
    reason: str | None = None
    banned_until: datetime | None = None
    lifted_at: datetime | None = None
    has_prior_ban: bool = False


class AdminUserOut(BaseModel):
    """Owner-facing user projection — DOES carry ``sub`` (different audience).

    ``is_owner`` lets the SPA hide ban/delete buttons on the owner's own row
    (the server guards too, but the UI should not offer the action).
    """

    user_uuid: UUID
    sub: str
    nick: str
    has_set_nick: bool
    is_owner: bool
    ban: BanStatusOut


class AdminUserListOut(BaseModel):
    """Paginated owner-list response (offset pagination, hobbyist scale).

    ``total`` + ``total_pages`` are included so the SPA renders pager controls
    without a second round-trip.
    """

    items: list[AdminUserOut]
    page: int
    page_size: int
    total: int
    total_pages: int


class BanIn(BaseModel):
    """``POST /v1/users/{sub}/ban`` body. Required free-text justification."""

    duration: Literal["1h", "1d", "7d", "30d"]
    reason: str = Field(min_length=5, max_length=500)

