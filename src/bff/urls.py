"""BFF URL configuration.

Mounts the OIDC redirect chain (login/callback/logout) at the URL root + the
django-ninja API under its ``/v1/`` version root + the SPA-shell index view.
Phase 5 split the version prefix from the OIDC chain: the API keeps ``/v1/``
(``/v1/me``, ``/v1/users``, ``PATCH /v1/me``) but the OIDC redirect chain lives
at the URL root (``/login``, ``/callback``, ``/logout``) so the OAuth URLs in
the Auth0 dashboard stay short. The module is still ``src/bff/`` (AGENTS.md §4
directory structure).

App name stays ``bff`` so ``reverse("bff:callback")`` / ``reverse("bff:index")``
keep working — internal Django URL names are not path segments. Auth0's Allowed
Callback / Allowed Logout URLs are updated to ``/callback`` / ``/`` in the
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
    # OIDC redirect chain — /login, /callback, /logout (Phase 5: dropped the
    # /v1 prefix so the OAuth URLs registered in the Auth0 dashboard stay short).
    path("login", login_view, name="login"),
    path("callback", callback, name="callback"),
    path("logout", logout, name="logout"),
    # django-ninja API under /v1/ (so /v1/me, /v1/users, PATCH /v1/me).
    path("v1/", api.urls),
    # Index — SPA shell document (S-01). ``reverse("bff:index")`` → ``/``.
    path("", index, name="index"),
]
