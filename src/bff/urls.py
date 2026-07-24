"""BFF URL configuration.

Mounts the OIDC redirect chain (login/callback/logout) + the django-ninja API
under ``api/`` (so ``/api/me``, ``/api/users``). The ``index`` route redirects
to ``/`` — Phase 5 replaces this with the template-rendering welcome/main view.

App name ``bff`` so templates can reverse ``bff:login`` etc.
"""
from __future__ import annotations

from django.shortcuts import redirect
from django.urls import path

from src.bff.api import api
from src.bff.routers.auth_routes import callback, login_view, logout
from src.bff.routers.owner_routes import router as owner_router
from src.bff.routers.session_routes import router as session_router

api.add_router("/", session_router)
api.add_router("/", owner_router)


def index(request):  # pragma: no cover — Phase 5 replaces this view.
    """Placeholder: redirect to ``/``. Phase 5 swaps in template dispatch."""
    return redirect("/")


urlpatterns = [
    # OIDC redirect chain.
    path("bff/login", login_view, name="login"),
    path("bff/callback", callback, name="callback"),
    path("bff/logout", logout, name="logout"),
    # django-ninja API under ``api/`` (so /api/me, /api/users).
    path("api/", api.urls),
    # Index — Phase 5 replaces with welcome/main template dispatch.
    path("", index, name="index"),
]
