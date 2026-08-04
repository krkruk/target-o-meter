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

from django.urls import path, re_path

from src.bff.api import api
from src.bff.routers.auth_routes import callback, login_view, logout
from src.bff.routers.owner_routes import router as owner_router
from src.bff.routers.scoring_routes import router as scoring_router
from src.bff.routers.session_routes import router as session_router
from src.bff.views import index, privacy

app_name = "bff"

api.add_router("/", session_router)
api.add_router("/", owner_router)
api.add_router("/", scoring_router)


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
    # Cookie policy — standalone server-rendered page (no SPA boot). Lives
    # before the catch-all AND is excluded from its negative lookahead so the
    # banner's "Cookie Policy" link resolves to the real page, not the shell.
    path("privacy", privacy, name="privacy"),
    # S-02: SPA client-side deep links (/dashboard, /waiting/:jobId, …) must
    # survive a refresh. A catch-all serves the index document for any non-API,
    # non-OIDC path so BrowserRouter picks up the route client-side. The
    # negative lookahead excludes the versioned API + the OIDC chain + the
    # standalone privacy page so a 404'ing /v1/whatever, /login-typos, or
    # /privacy-typos surfaces as 404 (not a false 200 that masks the routing
    # bug). /admin/ is matched at the ROOT urls.py level before this include
    # is even consulted.
    re_path(r"^(?!v1/|login|callback|logout|admin/|privacy).*$", index),
]
