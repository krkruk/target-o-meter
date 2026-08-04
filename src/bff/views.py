"""BFF view functions for the SPA shell (S-01).

``index`` serves the SPA bootstrap document regardless of auth state — the
SPA decides welcome vs. app shell client-side via ``GET /v1/me``. F-01's
welcome/main template dispatch is gone (the templates themselves are retired
in Phase 2/3 when the React swap lands; for Phase 1 the shell is a minimal
document carrying the root mount point).

The URL name ``bff:index`` is preserved (logout's ``returnTo`` reverses it).
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def index(request: HttpRequest) -> HttpResponse:
    """Serve the SPA shell document. Auth-state dispatch moved client-side."""
    return render(request, "base.html")


def privacy(request: HttpRequest) -> HttpResponse:
    """Serve the standalone cookie-policy page.

    Public (no auth decorator) — the policy must be reachable by
    unauthenticated visitors; that is the whole point of the banner's
    "Cookie Policy" link. The template does NOT extend ``base.html`` and does
    NOT mount the SPA, so it renders standalone regardless of auth state.
    """
    return render(request, "privacy.html")
