"""Health readiness endpoint for Railway's healthcheck prober (P1).

Railway's prober has no session and needs a real readiness signal that
distinguishes "gunicorn booted" from "ready to serve." ``/health`` returns
``200`` + body ``ok`` with no DB access and no auth, so a session-less
external prober can reach it.

Kept in its own module (not a grab-bag ``views.py``) so the deploy/operational
surface stays discoverable and any future health-adjacent view (e.g. a deeper
readiness check that DOES probe the DB) has an obvious home.
"""
from __future__ import annotations

from django.http import HttpRequest, HttpResponse


def health(request: HttpRequest) -> HttpResponse:
    """Return ``200 ok`` — the literal ``ok`` Railway's prober expects."""
    return HttpResponse("ok")
