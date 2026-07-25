"""Acceptance-test fixtures for the deferred UAT test (F-01 Phase 6.3).

These fixtures are what the later ``tests/acceptance/test_uat_auth_real_auth0.py``
(Q7, deferred to a later slice) will consume. They read Auth0 creds from env
and skip-on-missing so a missing secret never turns the build red.

``uat_auth0_creds`` is parametrizable across ``"user"`` / ``"owner"`` per Q8
(when the UAT test lands, it should cover both roles).
"""
from __future__ import annotations

import os

import pytest


def _creds_for_role(role: str) -> dict[str, str]:
    """Read ``AUTH0_UAT_<ROLE>_*`` env vars for the given role."""
    prefix = f"AUTH0_UAT_{role.upper()}_"
    keys = ("EMAIL", "PASSWORD", "SUB", "NICK")
    return {k.lower(): os.environ.get(f"{prefix}{k}", "") for k in keys}


@pytest.fixture
def uat_base_url() -> str:
    """The base URL the Playwright UAT test drives (local runserver by default)."""
    return os.environ.get("UAT_BASE_URL", "http://localhost:8000")


@pytest.fixture
def uat_auth0_creds(request) -> dict[str, str]:
    """Auth0 creds for a UAT role, parametrizable across ``user`` / ``owner``.

    Usage in the future test::

        @pytest.mark.parametrize("uat_auth0_creds", ["user", "owner"], indirect=True)
        def test_uat_login(uat_auth0_creds, ...): ...

    Skips if any required cred is empty — a missing secret must never fail CI.
    """
    role = getattr(request, "param", "user")
    creds = _creds_for_role(role)
    missing = [k for k, v in creds.items() if not v]
    if missing:
        pytest.skip(
            f"UAT skipped: AUTH0_UAT_{role.upper()}_* env vars missing: {missing}"
        )
    return creds
