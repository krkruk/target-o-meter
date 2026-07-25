"""Dev-only auth-bypass middleware (F-01 Phase 4).

Skip the Auth0 dance locally by auto-authenticating as a ``sub`` from the env.
Reuses ``OWNER_SUB_ID`` as the single role source (Q6 synthesis): set the two
equal to impersonate Owner locally.

TWO-LAYER GUARD (plan-review F2 — load-bearing):
  1. ``target_o_meter.E001`` (Phase 2.4) — boot-layer: refuses to start if
     ``DEV_AUTH_BYPASS_SUB`` is set while ``DEBUG=False``. Runs at
     ``manage.py check`` time.
  2. THIS middleware's ``if not settings.DEBUG: return`` — serving-layer:
     E001 only fires at check/runserver boot, NOT in the gunicorn serving loop
     on Render. So the middleware MUST self-gate on ``DEBUG=False`` too —
     otherwise a misconfigured prod env with ``DEV_AUTH_BYPASS_SUB`` set would
     serve with the bypass live (and setting it equal to ``OWNER_SUB_ID``
     would impersonate Owner). Belt-and-suspenders with E001.

Stateless: never touches the session, never calls ``login()``. SessionAuth
trusts ``request.user.is_authenticated`` (research §"Approach C"), so setting
``request.user`` directly is enough.

Env reads: ``DEV_AUTH_BYPASS_SUB`` is read from ``os.environ`` at request time
(NOT from the settings attribute cached at import). This matches how
``User.role`` reads ``OWNER_SUB_ID`` directly, and lets tests flip the env var
per-test via ``monkeypatch.setenv`` without reloading settings. ``DEBUG`` is
read from settings (it's a Django core setting, not a per-request toggle).
"""
from __future__ import annotations

import os

from django.conf import settings
from django.utils.deprecation import MiddlewareMixin


_dev_user = None  # module-level cache (immutable for process lifetime)


def _get_dev_user():
    """Return (cached) the dev-bypass user, creating the row if needed.

    Cached at module level so there's no per-request DB hit. The row is
    immutable for the process lifetime (the bypass sub comes from env, which
    doesn't change at runtime), so caching is safe. Tests that change the env
    var must clear this cache.
    """
    global _dev_user
    if _dev_user is None:
        from src.domains.identity.models import User
        sub = os.environ.get("DEV_AUTH_BYPASS_SUB", "")
        _dev_user = User.objects.get_or_create(
            sub=sub,
            defaults={"nick": f"dev-{sub[:8]}"},
        )[0]
    return _dev_user


class DevAuthBypassMiddleware(MiddlewareMixin):
    """Auto-authenticate as ``DEV_AUTH_BYPASS_SUB``'s user when DEBUG=True.

    Order matters: register IMMEDIATELY AFTER ``AuthenticationMiddleware`` so
    ``request.user`` starts as ``AnonymousUser`` and we overwrite it. If placed
    before, AuthenticationMiddleware would clobber our assignment.
    """

    def process_request(self, request):
        # FIRST LINE — serving-layer guard (plan-review F2). E001 is the
        # boot-layer guard; this is the belt-and-suspenders serving guard.
        if not settings.DEBUG:
            return None

        # Bypass not configured → no-op. Read env at request time (not the
        # settings attribute) so tests can flip it per-test.
        sub = os.environ.get("DEV_AUTH_BYPASS_SUB", "")
        if not sub:
            return None

        # Real OAuth login wins — if AuthenticationMiddleware already
        # authenticated the request (a real session), leave it alone.
        if getattr(request, "user", None) and request.user.is_authenticated:
            return None

        request.user = _get_dev_user()
        return None
