"""Demo owner route — proves the 401/403 RBAC contract end-to-end.

``GET /api/users`` is the one real route that exercises ``require_owner``:
  - anonymous     → 401 (``session_auth`` returns None → AuthenticationError)
  - authed User   → 403 (``require_owner`` raises ``HttpError(403)`` in the body)
  - authed Owner  → 200 ``[]`` (empty until S-04 adds real list/remove logic)

Real owner actions (list-all-users bodies, remove-user, invite-only toggle)
are S-04 (FR-003/004/005). F-01 ships only this proof + the helper.

Auth shape: ``session_auth`` as the ``auth`` callable (gates authentication),
``require_owner(request)`` called in the body (gates authorization). See
``bff/api.py`` for why this is NOT an ``auth=[session_auth, require_owner]``
list in this django-ninja version.
"""
from __future__ import annotations

from ninja import Router

from src.bff.api import require_owner, session_auth
from src.domains.identity.dtos import UserOut
from src.domains.identity.services import list_users


router = Router()


@router.get("/users", auth=session_auth, response={200: list[UserOut]})
def list_all_users(request) -> list[UserOut]:
    """Owner-only: list all users (no ``sub``). Empty until S-04.

    ``require_owner`` is the first body line — it raises ``HttpError(403)``
    before any work if the resolved user is not Owner. The 401 (anonymous)
    case is handled by ``session_auth`` before the body runs.
    """
    require_owner(request)
    return list_users()
