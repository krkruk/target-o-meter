"""``/v1/me`` — the SPA auth-state bootstrap + first-login nick mutation.

``GET /v1/me`` returns the logged-in user's nick + role + ``has_set_nick`` (no
``sub`` — Zero Email Storage) or 401. GET needs no CSRF token.

``PATCH /v1/me`` is the SPA's first-login nick endpoint (FR-002). Body
``{nick}``; on success returns the refreshed ``MeOut`` (nick updated,
``has_set_nick=True``). CSRF is auto-enforced by ``SessionAuth`` (extends
``APIKeyCookie``, ``csrf=True``) — no extra middleware. Declares 409 for the
CI-uniqueness collision (``NickTakenError`` → ``HttpError(409)``).

Declares ONLY ``200: MeOut`` on the GET — a failed auth callable raises
``AuthenticationError`` routed through django-ninja's default handler →
``{"detail": "Unauthorized"}``, never a serialized ``MeOut`` (plan-review F4:
do NOT declare ``401: MeOut``).

Impl-review F2: a session whose ``sub`` has no row (S-04 deletion, Auth0
tenant migration) raises ``User.DoesNotExist`` inside the service — we map
that to ``HttpError(401)`` to honor the documented contract and force a
clean re-login rather than surfacing an opaque 500.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from ninja import Router
from ninja.errors import HttpError

from src.bff.api import session_auth
from src.domains.identity.dtos import ErrorOut, MeOut, NickIn, UserOut
from src.domains.identity.services import get_user_context, set_nick
from src.domains.identity.services import NickTakenError


router = Router()


@router.get("/me", auth=session_auth, response={200: MeOut})
def me(request) -> MeOut:
    try:
        dto = get_user_context(str(request.user.sub))
    except get_user_model().DoesNotExist:
        raise HttpError(401, "Session user no longer exists") from None
    return MeOut(
        authenticated=True,
        user=UserOut(nick=dto.nick, role=dto.role, has_set_nick=dto.has_set_nick),
    )


@router.patch(
    "/me",
    auth=session_auth,
    response={200: MeOut, 409: ErrorOut},
)
def patch_me(request, payload: NickIn) -> MeOut:
    """First-login nick mutation (FR-002). CSRF-enforced via ``SessionAuth``."""
    try:
        dto = set_nick(str(request.user.sub), payload.nick)
    except get_user_model().DoesNotExist:
        raise HttpError(401, "Session user no longer exists") from None
    except NickTakenError:
        raise HttpError(409, "Nick already taken") from None
    return MeOut(
        authenticated=True,
        user=UserOut(nick=dto.nick, role=dto.role, has_set_nick=dto.has_set_nick),
    )
