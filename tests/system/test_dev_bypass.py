"""System test: dev-auth-bypass middleware + admin registration (Phase 4).

Covers the manual checks:
  - 4.4 Bypass auto-authenticates (curl /api/me → 200 with DEV_AUTH_BYPASS_SUB)
  - 4.5 identity_user is registered in admin, role/is_owner read-only

The bypass middleware self-gates on ``settings.DEBUG`` and ``DEV_AUTH_BYPASS_SUB``.
We exercise it through the Django test client with ``DEBUG=True`` monkey-patched
and the env var set — proving the same path ``runserver`` would take.
"""
from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib import admin

from src.domains.identity.models import User


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


def test_bypass_auto_authenticates_when_configured(
    client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With DEBUG=True + DEV_AUTH_BYPASS_SUB set, /api/me returns 200 with no
    Auth0 call (the middleware populated request.user).

    This is the manual check 4.4 expressed as a repeatable test. We set
    OWNER_SUB_ID equal to the bypass sub to also prove the impersonation
    path (Q6 synthesis: set the two equal to act as Owner).
    """
    monkeypatch.setattr(settings, "DEBUG", True)
    bypass_sub = "auth0|dev-bypass"
    monkeypatch.setenv("DEV_AUTH_BYPASS_SUB", bypass_sub)
    # Impersonation: bypass sub == owner sub → the dev user IS the owner.
    monkeypatch.setenv("OWNER_SUB_ID", bypass_sub)

    # Clear the module-level cache so the new env takes effect.
    from src.target_o_meter import dev_auth_bypass
    dev_auth_bypass._dev_user = None

    response = client.get("/api/me")
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    # The bypass nick is "dev-" + first 8 chars of the sub.
    assert body["user"]["nick"].startswith("dev-")
    # Impersonation worked — the dev bypass user is the Owner.
    assert body["user"]["role"] == "owner"


def test_bypass_is_inert_when_sub_unset(
    client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With DEV_AUTH_BYPASS_SUB unset, the middleware no-ops → /api/me is 401."""
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.delenv("DEV_AUTH_BYPASS_SUB", raising=False)

    from src.target_o_meter import dev_auth_bypass
    dev_auth_bypass._dev_user = None

    response = client.get("/api/me")
    assert response.status_code == 401


def test_identity_user_is_registered_in_admin() -> None:
    """Manual check 4.5: identity_user is in the admin index (registered).

    Also asserts ``role`` / ``is_owner`` are read-only fields — they're
    derived from OWNER_SUB_ID and must never be editable in the GUI.
    """
    assert User in admin.site._registry, "identity.User must be registered in admin"

    registration = admin.site._registry[User]
    readonly = set(registration.readonly_fields or [])
    assert "role" in readonly, "role must be read-only (derived from OWNER_SUB_ID)"
    assert "is_owner" in readonly, "is_owner must be read-only (derived from OWNER_SUB_ID)"


def test_bypass_user_row_created_idempotently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bypass creates the dev-user row on first hit; second hit reuses it.

    Guards the module-level cache + get_or_create contract.
    """
    monkeypatch.setattr(settings, "DEBUG", True)
    bypass_sub = "auth0|idempotent-bypass"
    monkeypatch.setenv("DEV_AUTH_BYPASS_SUB", bypass_sub)

    from src.target_o_meter import dev_auth_bypass
    dev_auth_bypass._dev_user = None

    user1 = dev_auth_bypass._get_dev_user()
    user2 = dev_auth_bypass._get_dev_user()
    assert user1.id == user2.id, "second call must reuse the cached row"
    assert User.objects.filter(sub=bypass_sub).count() == 1
