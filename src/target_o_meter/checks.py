"""Project-level Django system checks (F-01 production-safety guards).

Four layered checks registered with ``@register(Tags.security)`` (NOT
``deploy=True`` — see ``check_dev_auth_bypass_not_in_prod`` docstring for why):

  - ``target_o_meter.E001`` — HARD: dev-auth-bypass active in a prod-shaped
    config (``DEV_AUTH_BYPASS_SUB`` set + ``DEBUG=False``) → refuses to boot.
  - ``target_o_meter.E002`` — HARD: ``SECRET_KEY`` resolves to the insecure
    ``django-insecure-…`` fallback while ``DEBUG=False`` → refuses to boot
    (session forgery / CSRF bypass worst case).
  - ``target_o_meter.W001`` — SOFT: ``OWNER_SUB_ID`` empty in prod → warns.
  - ``target_o_meter.W002`` — SOFT: ``DEBUG=True`` while ``APP_BASE_URL``
    points at a non-localhost host (a prod-shaped deploy URL) → warns.

Registered by importing this module from the bottom of ``settings.py``.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register


# The literal fallback from ``settings.SECRET_KEY``. Comparing against this
# prefix (Django's own insecure-key convention) catches the unmodified repo
# default without false-firing on a user-supplied key that happens to start
# with the same prefix — a real key set via SECRET_KEY/AUTH0_SECRET never
# reaches the fallback branch.
_INSECURE_SECRET_KEY_PREFIX = "django-insecure-"

# Hosts that are unambiguously local dev. ``APP_BASE_URL`` pointing anywhere
# else while ``DEBUG=True`` is the prod-shaped-config signal for W002.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _app_base_url_host() -> str:
    """Return the hostname of ``APP_BASE_URL`` (or "" if unset/unparseable).

    Read from ``os.environ`` (not ``settings``) so this stays in lockstep with
    ``settings._allowed_hosts``/``APP_BASE_URL`` resolution — the value is
    never materialised as a Django setting attribute.
    """
    base = os.environ.get("APP_BASE_URL", "") or ""
    if not base:
        return ""
    return (urlparse(base).hostname or "").lower()


@register(Tags.security)
def check_dev_auth_bypass_not_in_prod(app_configs, **kwargs):
    """E001 — refuse to boot if the dev-auth-bypass is live in a prod config.

    Why a plain ``Error`` (not ``@register(..., deploy=True)``): a
    ``deploy=True`` check ONLY runs under ``manage.py check --deploy`` and
    would NOT fire on ``runserver`` or the WSGI/gunicorn serving loop. A plain
    ``@register(Tags.security)`` check runs on every ``manage.py`` command
    (``check``, ``runserver``, ``migrate``, …) and prevents Django commands
    from running at all when the bypass is misconfigured. Plan-review F2: the
    DevAuthBypassMiddleware DEBUG gate is the *serving-layer* backstop; this
    check is the *boot-layer* guard. A deploy/release pipeline MUST also run
    ``manage.py check`` as a gate before promoting (flagged in Migration Notes).
    """
    if getattr(settings, "DEV_AUTH_BYPASS_SUB", "") and not settings.DEBUG:
        return [Error(
            "DEV_AUTH_BYPASS_SUB is set while DEBUG=False — the dev-auth-bypass "
            "would be live in a production-shaped config. Unset "
            "DEV_AUTH_BYPASS_SUB or run with DEBUG=True (local dev only).",
            id="target_o_meter.E001",
        )]
    return []


@register(Tags.security)
def check_secret_key_not_insecure_in_prod(app_configs, **kwargs):
    """E002 — refuse to boot if SECRET_KEY is the insecure fallback in prod.

    ``settings.SECRET_KEY`` falls back to a hardcoded ``django-insecure-…``
    value so a fresh checkout runs without env vars. That key is public in the
    repo; signing session cookies / CSRF tokens with it in a prod-shaped config
    (``DEBUG=False``) is a session-forgery and CSRF-bypass class. The dev
    fallback is fine locally (``DEBUG=True``); this check fires only when the
    fallback survives into a prod-shaped config because the deploy forgot
    ``SECRET_KEY`` / ``AUTH0_SECRET``. Predicate is the prefix, not the whole
    literal, so rotating the dev default doesn't silently disable the check.
    """
    secret = getattr(settings, "SECRET_KEY", "") or ""
    if not settings.DEBUG and secret.startswith(_INSECURE_SECRET_KEY_PREFIX):
        return [Error(
            "SECRET_KEY resolves to the insecure 'django-insecure-…' fallback "
            "while DEBUG=False — session cookies and CSRF tokens would be "
            "signed with a publicly-known key. Set SECRET_KEY (or AUTH0_SECRET) "
            "to a generated value before serving production traffic.",
            id="target_o_meter.E002",
        )]
    return []


@register(Tags.security)
def check_owner_sub_id_set(app_configs, **kwargs):
    """W001 — warn (not block) if OWNER_SUB_ID is empty in prod.

    Empty Owner → no one can reach owner-only routes (fail-closed by design,
    research §7), so a warning is the right severity: the app boots, but the
    Owner role is inert until the env var is configured. Dev (DEBUG=True) is
    exempt — local dev often runs without an owner.
    """
    if not getattr(settings, "OWNER_SUB_ID", "") and not settings.DEBUG:
        return [Warning(
            "OWNER_SUB_ID is empty while DEBUG=False — the Owner role is "
            "inert (no user can satisfy owner-only checks) until it is set.",
            id="target_o_meter.W001",
        )]
    return []


@register(Tags.security)
def check_debug_not_on_for_prod_url(app_configs, **kwargs):
    """W002 — warn if DEBUG=True while APP_BASE_URL is a non-localhost host.

    ``DEBUG=True`` leaks full stack traces and exposes the static-file
    listing. Local dev binds to localhost; if ``APP_BASE_URL`` (the canonical
    deploy URL) points anywhere else while ``DEBUG=True``, the config looks
    prod-shaped with debug left on. Warning (not Error): the app still boots
    — the deployer may be mid-config — but the gate surfaces before any
    request is served. Exempt when ``APP_BASE_URL`` is unset (pure local dev
    with no deploy URL configured).
    """
    if not settings.DEBUG:
        return []
    host = _app_base_url_host()
    if host and host not in _LOCAL_HOSTS:
        return [Warning(
            "DEBUG=True while APP_BASE_URL points at a non-localhost host "
            f"({host!r}) — full stack traces and the static-file listing "
            "would be exposed. Set DEBUG=False before serving production "
            "traffic.",
            id="target_o_meter.W002",
        )]
    return []
