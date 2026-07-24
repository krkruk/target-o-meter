"""``/api/me`` — the SPA auth-state bootstrap.

Returns the logged-in user's nick + role (no ``sub`` — Zero Email Storage) or
401. GET needs no CSRF token. Declares ONLY ``200: MeOut`` — a failed auth
callable raises ``AuthenticationError`` routed through django-ninja's default
handler → ``{"detail": "Unauthorized"}``, never a serialized ``MeOut``
(plan-review F4: do NOT declare ``401: MeOut``).
"""
from __future__ import annotations

from ninja import Router

from src.bff.api import session_auth
from src.domains.identity.dtos import MeOut, UserOut
from src.domains.identity.services import get_user_context


router = Router()


@router.get("/me", auth=session_auth, response={200: MeOut})
def me(request) -> MeOut:
    dto = get_user_context(str(request.user.sub))
    return MeOut(
        authenticated=True,
        user=UserOut(nick=dto.nick, role=dto.role),
    )
