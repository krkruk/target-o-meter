"""``/api/me`` — the SPA auth-state bootstrap.

Returns the logged-in user's nick + role (no ``sub`` — Zero Email Storage) or
401. GET needs no CSRF token. Declares ONLY ``200: MeOut`` — a failed auth
callable raises ``AuthenticationError`` routed through django-ninja's default
handler → ``{"detail": "Unauthorized"}``, never a serialized ``MeOut``
(plan-review F4: do NOT declare ``401: MeOut``).

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
from src.domains.identity.dtos import MeOut, UserOut
from src.domains.identity.services import get_user_context


router = Router()


@router.get("/me", auth=session_auth, response={200: MeOut})
def me(request) -> MeOut:
    try:
        dto = get_user_context(str(request.user.sub))
    except get_user_model().DoesNotExist:
        raise HttpError(401, "Session user no longer exists") from None
    return MeOut(
        authenticated=True,
        user=UserOut(nick=dto.nick, role=dto.role),
    )
