"""BFF URL configuration.

Mounts the OIDC redirect chain (login/callback/logout) + the django-ninja API
+ the SPA-shell index view under a single ``/v1/`` version root (S-01 renamed
the surface: the ``bff/`` and ``api/`` prefixes are gone). The module is still
``src/bff/`` (AGENTS.md §4 directory structure); only the URL tree changed.

App name stays ``bff`` so ``reverse("bff:callback")`` / ``reverse("bff:index")``
keep working — internal Django URL names are not path segments. Auth0's Allowed
Callback / Allowed Logout URLs are updated to ``/v1/callback`` / ``/`` in the
dashboard (plan Phase 4).
"""

from __future__ import annotations

from django.urls import path

from src.bff.api import api
from src.bff.routers.auth_routes import callback, login_view, logout
from src.bff.routers.owner_routes import router as owner_router
from src.bff.routers.session_routes import router as session_router
from src.bff.views import index

app_name = "bff"

api.add_router("/", session_router)
api.add_router("/", owner_router)


urlpatterns = [
    # OIDC redirect chain — /v1/login, /v1/callback, /v1/logout.
    path("v1/login", login_view, name="login"),
    path("v1/callback", callback, name="callback"),
    path("v1/logout", logout, name="logout"),
    # django-ninja API under /v1/ (so /v1/me, /v1/users, PATCH /v1/me).
    path("v1/", api.urls),
    # Index — SPA shell document (S-01). ``reverse("bff:index")`` → ``/``.
    path("", index, name="index"),
]
