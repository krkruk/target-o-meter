"""S-04 owner routes — list / ban / unban / delete, all behind ``require_owner``.

Mirrors the F-01 demo route's auth shape: ``auth=session_auth`` (gates
authentication) + ``require_owner(request)`` as the first body line (gates
authorization). CSRF is auto-enforced by django-ninja's SessionAuth on the
non-GET verbs — no new middleware (proven by S-01's ``PATCH /v1/me`` tests).

The list response is the paginated ``AdminUserListOut`` (carries ``sub`` — the
owner audience needs it, unlike the sub-less ``UserOut`` on ``/v1/me``). Ban /
unban / delete map the domain's typed exceptions to HTTP statuses (404 / 409 /
422); the router never touches the ORM.
"""

from __future__ import annotations

from ninja import Router, Status
from ninja.errors import HttpError

from src.bff.api import require_owner, session_auth
from src.domains.identity.dtos import (
    AdminUserListOut,
    BanIn,
    BanStatusOut,
    ErrorOut,
)
from src.domains.identity.services import (
    ActiveBanExistsError,
    CannotModifyOwnerError,
    NoActiveBanError,
    UserNotFoundError,
    ban_user,
    delete_user,
    list_users_for_owner,
    unban_user,
)


router = Router()


@router.get("/users", auth=session_auth, response={200: AdminUserListOut})
def list_all_users(request, q: str = "", page: int = 1, page_size: int = 20) -> AdminUserListOut:
    """Owner-only: paginated + searchable list of users with ban status.

    ``require_owner`` is the first body line — it raises ``HttpError(403)``
    before any work if the resolved user is not Owner. The 401 (anonymous)
    case is handled by ``session_auth`` before the body runs.
    """
    require_owner(request)
    return list_users_for_owner(q=q, page=page, page_size=page_size)


@router.post(
    "/users/{user_sub}/ban",
    auth=session_auth,
    response={200: BanStatusOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut},
)
def ban_a_user(request, user_sub: str, payload: BanIn) -> BanStatusOut:
    """Owner-only: ban ``user_sub`` with a duration + required free-text reason.

    Domain exceptions map: ``UserNotFoundError → 404``,
    ``CannotModifyOwnerError → 409`` (cannot ban the owner),
    ``ActiveBanExistsError → 409`` (lift the active ban first),
    ``ValueError → 422`` (invalid duration / short reason — the DTO also guards).
    """
    require_owner(request)
    try:
        return ban_user(
            user_sub=user_sub, duration=payload.duration, reason=payload.reason
        )
    except UserNotFoundError:
        raise HttpError(404, "User not found") from None
    except CannotModifyOwnerError:
        raise HttpError(409, "Cannot ban the owner") from None
    except ActiveBanExistsError:
        raise HttpError(409, "User is already banned") from None
    except ValueError:
        raise HttpError(422, "Invalid ban payload") from None


@router.post(
    "/users/{user_sub}/unban",
    auth=session_auth,
    response={200: BanStatusOut, 404: ErrorOut, 409: ErrorOut},
)
def unban_a_user(request, user_sub: str) -> BanStatusOut:
    """Owner-only: lift the active ban on ``user_sub`` (sets ``lifted_at``).

    ``UserNotFoundError → 404``, ``NoActiveBanError → 409``.
    """
    require_owner(request)
    try:
        return unban_user(user_sub=user_sub)
    except UserNotFoundError:
        raise HttpError(404, "User not found") from None
    except NoActiveBanError:
        raise HttpError(409, "No active ban") from None


@router.delete(
    "/users/{user_sub}",
    auth=session_auth,
    response={204: None, 404: ErrorOut, 409: ErrorOut},
)
def delete_a_user(request, user_sub: str):
    """Owner-only: hard-delete ``user_sub`` (cascades to its ``Ban`` rows).

    ``UserNotFoundError → 404``, ``CannotModifyOwnerError → 409`` (cannot delete
    the owner). The service reuses the same owner-guard as ban.
    """
    require_owner(request)
    try:
        delete_user(user_sub=user_sub)
    except UserNotFoundError:
        raise HttpError(404, "User not found") from None
    except CannotModifyOwnerError:
        raise HttpError(409, "Cannot delete the owner") from None
    return Status(204, None)
