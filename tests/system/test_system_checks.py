"""System test for the F-01 production-safety checks (E001 / W001).

These are the manual checks from Phase 2's plan, expressed as repeatable
system tests so the E001 boot-block and W001 warning can never silently
regress. Uses Django's check registry directly (not subprocess) for speed.

Covers:
  - 2.2 E001 fires (DEBUG=False + DEV_AUTH_BYPASS_SUB set)
  - 2.3 W001 fires (DEBUG=False + empty OWNER_SUB_ID)
  - dev config (DEBUG=True) is clean — neither fires
"""
from __future__ import annotations

import pytest
from django.conf import settings
from django.core.checks import Error, Warning, run_checks

pytestmark = pytest.mark.django_db


def _ids(checks: list) -> set[str]:
    """Extract the check IDs from a list of Error/Warning objects."""
    return {c.id for c in checks}


def test_e001_fires_when_dev_bypass_set_in_prod_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """E001: ``DEV_AUTH_BYPASS_SUB`` set + ``DEBUG=False`` → boot-blocking Error.

    This is the load-bearing prod guard (plan-review F2): a misconfigured prod
    env with the dev-bypass set must refuse to boot.
    """
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "DEV_AUTH_BYPASS_SUB", "auth0|leaked-bypass")
    results = run_checks()
    errors = [c for c in results if isinstance(c, Error)]
    assert "target_o_meter.E001" in _ids(errors), (
        f"E001 must fire when DEV_AUTH_BYPASS_SUB is set under DEBUG=False; "
        f"got error ids: {_ids(errors)}"
    )


def test_e001_does_not_fire_when_debug_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """E001 is inert in dev (DEBUG=True) — the bypass is legitimate there."""
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(settings, "DEV_AUTH_BYPASS_SUB", "auth0|dev-bypass")
    results = run_checks()
    errors = [c for c in results if isinstance(c, Error)]
    assert "target_o_meter.E001" not in _ids(errors)


def test_w001_fires_when_owner_sub_empty_in_prod_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """W001: empty ``OWNER_SUB_ID`` + ``DEBUG=False`` → warning (not block).

    The Owner role is inert until configured; a warning is the right severity.
    """
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "OWNER_SUB_ID", "")
    results = run_checks()
    warnings = [c for c in results if isinstance(c, Warning)]
    assert "target_o_meter.W001" in _ids(warnings), (
        f"W001 must fire when OWNER_SUB_ID is empty under DEBUG=False; "
        f"got warning ids: {_ids(warnings)}"
    )


def test_w001_does_not_fire_when_owner_sub_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """W001 is inert once OWNER_SUB_ID is configured."""
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "OWNER_SUB_ID", "auth0|configured-owner")
    results = run_checks()
    warnings = [c for c in results if isinstance(c, Warning)]
    assert "target_o_meter.W001" not in _ids(warnings)
