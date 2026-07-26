"""Custom ``runserver`` that proxies staticfiles misses to the Vite dev server.

Phase 5 bugfix: Django's stock ``runserver`` wraps the WSGI app in
``StaticFilesHandler``, which claims every ``/static/...`` request and tries
the staticfiles finders. For an asset that lives only in Vite's module graph
(e.g. ``assets/target.svg`` imported by a component), the finders miss and the
handler returns 404 — without passing the request through to the URLconf. So a
URL-level proxy can't catch it; we replace the handler itself.

This command is a one-line override of ``get_handler``: instead of wrapping
with ``StaticFilesHandler``, wrap with
``src.bff.dev_vite_proxy.ViteProxyStaticFilesHandler`` — a subclass that proxies
misses to Vite (``http://localhost:5173/static/<path>``). The base
``Command.__init__`` / argument parsing / autoreloader all come from the parent
unchanged.

Prod never runs ``runserver`` (gunicorn serves via WhiteNoise), so this is
dev-only by construction. It is, however, the command the system tests boot —
so the Vite-proxy behavior is exercised end-to-end on every prod-mode test.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.staticfiles.management.commands.runserver import (
    Command as StaticFilesRunserverCommand,
)

from src.bff.dev_vite_proxy import ViteProxyStaticFilesHandler


class Command(StaticFilesRunserverCommand):
    """Identical to the staticfiles ``runserver`` but with a Vite-proxing
    static handler. See module docstring for the why."""

    def get_handler(self, *args, **options):
        """Return the WSGI handler wrapped with ``ViteProxyStaticFilesHandler``.

        Mirrors the parent's gate exactly (``use_static_handler`` AND
        (``DEBUG`` OR ``insecure_serving``)) so prod-shaped ``runserver`` boots
        with ``DEBUG=False`` don't activate the wrapper — there's no Vite to
        proxy to in prod, and WhiteNoise owns static serving there. We
        substitute our Vite-proxying subclass only when the parent would have
        wrapped at all.
        """
        handler = super(StaticFilesRunserverCommand, self).get_handler(
            *args, **options
        )
        use_static_handler = options["use_static_handler"]
        insecure_serving = options["insecure_serving"]
        if use_static_handler and (settings.DEBUG or insecure_serving):
            return ViteProxyStaticFilesHandler(handler)
        return handler
