"""django-ninja NinjaAPI instance + shared auth + authorization helpers.

django-ninja 1.6.x auto-enables CSRF for cookie-based auth (SessionAuth) —
there is no ``NinjaAPI(csrf=True)`` parameter in this version. CSRF is on for
every state-changing request that reaches a SessionAuth-protected route; GET
reads (``/api/me``) need no token.

Auth model:
  - ``session_auth`` — django-ninja's built-in SessionAuth. Reads ``sessionid``,
    checks ``request.user.is_authenticated``. Falsy return → 401. On success
    ``request.user`` is populated (by AuthenticationMiddleware).
  - ``require_owner(request)`` — an *authorization* helper called inside the
    route body (this version of django-ninja has no ``Depends``). Raises
    ``HttpError(403)`` if the resolved user is not Owner; returns the DTO
    otherwise. The route MUST also carry ``auth=session_auth`` so anonymous
    requests are rejected with 401 before the body runs.

The 401/403 split: anonymous → ``session_auth`` returns None → 401 (body never
runs). Authed non-Owner → body calls ``require_owner`` → 403. Owner → 200.

Why a body call and not an ``auth=[...]`` list: django-ninja's auth list
semantics are "try each callable until one returns truthy; if one *raises*,
short-circuit to the exception handler." A raising ``require_owner`` in the
list would mis-fire for anonymous requests (it runs before ``session_auth``
populates ``request.user``, hitting ``AnonymousUser.sub`` → 500, not 401).
Calling it in the body, after ``session_auth`` has gated, is the correct shape
for this version. (Newer django-ninja adds ``Depends``; S-01's React work can
migrate to it if the version is pinned up.)
"""
from __future__ import annotations

from ninja import NinjaAPI
from ninja.errors import HttpError
from ninja.security import SessionAuth

from src.domains.identity.dtos import UserContextDTO
from src.domains.identity.services import get_user_context

api = NinjaAPI(title="Target-o-meter BFF")

session_auth = SessionAuth()


def require_owner(request) -> UserContextDTO:
    """Authorization helper: resolve the acting user, 403 if not Owner.

    Call inside the route body (after ``auth=session_auth`` has guaranteed
    ``request.user`` is authenticated). Returns the DTO so the route can use
    it if needed; raising is the 403 mechanism.
    """
    dto = get_user_context(str(request.user.sub))
    if not dto.is_owner:
        raise HttpError(403, "Owner privileges required")
    return dto
