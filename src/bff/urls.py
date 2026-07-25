"""BFF URL configuration.

Mounts the OIDC redirect chain (login/callback/logout) + the django-ninja API
under ``api/`` (so ``/api/me``, ``/api/users``) + the template-rendered index
view (welcome/main dispatch, Phase 5).

App name ``bff`` so templates can reverse ``bff:login`` etc.
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
    # OIDC redirect chain.
    path("bff/login", login_view, name="login"),
    path("bff/callback", callback, name="callback"),
    path("bff/logout", logout, name="logout"),
    # django-ninja API under ``api/`` (so /api/me, /api/users).
    path("api/", api.urls),
    # Index — welcome/main template dispatch (Phase 5).
    path("", index, name="index"),
]
