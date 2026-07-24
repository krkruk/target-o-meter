"""Project-level Django system checks (F-01 production-safety guards).

Two layered checks registered with ``@register(Tags.security)`` (NOT
``deploy=True`` — see ``check_dev_auth_bypass_not_in_prod`` docstring for why):

  - ``target_o_meter.E001`` — HARD: dev-auth-bypass active in a prod-shaped
    config (``DEV_AUTH_BYPASS_SUB`` set + ``DEBUG=False``) → refuses to boot.
  - ``target_o_meter.W001`` — SOFT: ``OWNER_SUB_ID`` empty in prod → warns.

Registered by importing this module from the bottom of ``settings.py``.
"""
from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register


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
