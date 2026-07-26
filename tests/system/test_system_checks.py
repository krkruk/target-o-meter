"""System test for the production-safety checks (E001 / E002 / W001 / W002).

These are the boot-layer guards from ``src/target_o_meter/checks.py``,
expressed as repeatable system tests so a regression that silently drops one
fails here. Uses Django's check registry directly (not subprocess) for speed.

Covers:
  - E001 fires (DEBUG=False + DEV_AUTH_BYPASS_SUB set)
  - E002 fires (DEBUG=False + SECRET_KEY is the insecure fallback)
  - W001 fires (DEBUG=False + empty OWNER_SUB_ID)
  - W002 fires (DEBUG=True + APP_BASE_URL is a non-localhost host)
  - dev config (DEBUG=True, localhost) is clean — none of the prod-only
    checks fire
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


def test_e002_fires_when_secret_key_is_insecure_fallback_in_prod_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E002: SECRET_KEY resolves to the ``django-insecure-…`` fallback under
    DEBUG=False → boot-blocking Error.

    The deploy forgot to set SECRET_KEY/AUTH0_SECRET, so cookies would be
    signed with the public repo key. Worst case: session forgery + CSRF bypass.
    """
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(
        settings,
        "SECRET_KEY",
        "django-insecure-0n%b*1&a_*5va-)s1tv8e+98yzsb=o*f!7w%h#puwwsjz6dlq6",
    )
    results = run_checks()
    errors = [c for c in results if isinstance(c, Error)]
    assert "target_o_meter.E002" in _ids(errors), (
        f"E002 must fire when SECRET_KEY is the insecure fallback under "
        f"DEBUG=False; got error ids: {_ids(errors)}"
    )


def test_e002_does_not_fire_when_secret_key_is_real(monkeypatch: pytest.MonkeyPatch) -> None:
    """E002 is inert once a real (non-fallback) SECRET_KEY is set in prod."""
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "SECRET_KEY", "a-real-secret-not-the-fallback")
    results = run_checks()
    errors = [c for c in results if isinstance(c, Error)]
    assert "target_o_meter.E002" not in _ids(errors), (
        f"E002 must not fire for a real SECRET_KEY; got error ids: {_ids(errors)}"
    )


def test_e002_does_not_fire_when_debug_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """E002 is inert in dev — the insecure fallback is legitimate there."""
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(settings, "SECRET_KEY", "django-insecure-dev-only-key")
    results = run_checks()
    errors = [c for c in results if isinstance(c, Error)]
    assert "target_o_meter.E002" not in _ids(errors)


def test_w002_fires_when_debug_true_with_non_localhost_app_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W002: DEBUG=True + APP_BASE_URL pointing at a non-localhost host → warn.

    A prod-shaped deploy URL with debug left on leaks stack traces + the
    static-file listing. Warning (not Error): the app still boots so the
    deployer can finish configuring.
    """
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setenv("APP_BASE_URL", "https://target-o-meter.example.com")
    results = run_checks()
    warnings = [c for c in results if isinstance(c, Warning)]
    assert "target_o_meter.W002" in _ids(warnings), (
        f"W002 must fire when DEBUG=True and APP_BASE_URL is non-localhost; "
        f"got warning ids: {_ids(warnings)}"
    )


def test_w002_does_not_fire_when_app_base_url_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """W002 is inert in pure local dev — APP_BASE_URL unset means no deploy URL.

    Keeps ``DEBUG=True`` + no APP_BASE_URL (the canonical fresh-checkout dev
    config) silent.
    """
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    results = run_checks()
    warnings = [c for c in results if isinstance(c, Warning)]
    assert "target_o_meter.W002" not in _ids(warnings)


def test_w002_does_not_fire_when_app_base_url_is_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    """W002 is inert when APP_BASE_URL points at localhost (local dev URL)."""
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:8000")
    results = run_checks()
    warnings = [c for c in results if isinstance(c, Warning)]
    assert "target_o_meter.W002" not in _ids(warnings)
