"""System test: the ``/health`` readiness endpoint (infrastructure-as-code P1).

Railway's healthcheck prober needs a real readiness signal that distinguishes
"gunicorn booted" from "ready to serve" — process liveness alone returns true
before migrate finishes. The ``/health`` view returns ``200`` + body ``ok``
with no DB access and no auth gating, so Railway's session-less prober can
reach it. Routed at the project URLconf (``src/target_o_meter/urls.py``)
BEFORE the BFF catch-all so the SPA-shell ``re_path`` does not swallow it.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.django_db, pytest.mark.dev]


def test_health_returns_200_ok(client) -> None:
    """GET ``/health`` returns 200 with body ``ok`` — Railway's readiness signal.

    The view has no DB access and no auth gating; Railway's prober has no
    session and would 401 on any auth-required surface. Must NOT fall through
    to the BFF catch-all (which serves the SPA-shell HTML, not the literal
    ``ok`` Railway's prober expects).
    """
    response = client.get("/health")
    assert response.status_code == 200, response.status_code
    assert response.content == b"ok"
