"""BFF view functions for the template-rendered shell (F-01 Phase 5).

``index`` dispatches: anonymous → welcome page; authenticated → main page.
F-01 ships only this shell text — dashboard content is S-01. The full URL
contract is identical either way, so S-01 swaps templates for React with zero
endpoint churn.
"""
from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def index(request: HttpRequest) -> HttpResponse:
    """Dispatch on auth state: anonymous → welcome, authenticated → main."""
    if request.user.is_authenticated:
        return render(request, "main.html")
    return render(request, "welcome.html")
