"""Dev-only Vite proxy + the runserver handler that wires it (Phase 5 bugfix).

The motivating bug (``make dev`` at ``http://localhost:8000``): the browser
loads the SPA shell from Django (``:8000``) but the JS modules from Vite
(``:5173``). Vite rewrites asset imports like
``import targetUrl from '../../assets/target.svg'`` into bare absolute paths
(``/static/assets/target.svg?import``) — and browsers resolve bare absolute
paths against the **document's origin** (``:8000``), not the importing module's
origin (``:5173``). Setting Vite's ``base`` to a full origin URL does NOT fix
this: Vite's module-graph import rewriting always strips the origin and emits
``/static/...`` paths. So the request for ``/static/assets/target.svg`` hits
Django. Django's runserver wraps the WSGI app in ``StaticFilesHandler``, which
claims every ``/static/...`` request and tries the staticfiles finders; for an
asset that lives only in Vite's module graph the finders miss, the handler
404s, and — critically — does NOT pass the request through to the inner
URLconf. The welcome-page hero SVG never renders.

A URLconf-level proxy can't catch this (``StaticFilesHandler`` short-circuits
before the URLconf runs). The deterministic fix is to replace the runserver's
``StaticFilesHandler`` with one that proxies misses to Vite. Django's
``runserver`` command is the documented extension point: a subclass overriding
``get_handler`` returns our ``ViteProxyStaticFilesHandler``, which first tries
the normal staticfiles lookup and on miss forwards to Vite
(``http://localhost:5173/static/<path>``). In prod, gunicorn never invokes
``runserver`` and WhiteNoise serves the collected bundle same-origin, so this
code is dev-only by construction.

Lives in the BFF per AGENTS.md §5 (HTTP handling). Uses ``urllib.request``
(standard library) so the proxy adds no runtime dependency.
"""
from __future__ import annotations

import urllib.error
import urllib.request

from django.conf import settings
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.http import HttpRequest, HttpResponse

# Hop-by-hop headers RFC 2616 §13.5.1 says proxies MUST NOT forward. Copying
# these verbatim would let a client set, e.g., ``Connection: close`` on the
# upstream socket and break keep-alive in subtle ways.
_HOP_BY_HOP = frozenset(
    h.lower() for h in (
        "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade",
    )
)

# Headers the client sets that we must not blindly forward to Vite (they
# describe the client→Django hop, not the Django→Vite hop).
_CLIENT_ONLY = frozenset(("host", "content-length"))


def _proxy_to_vite(request: HttpRequest, path: str) -> HttpResponse:
    """Forward ``/static/<path>`` to the Vite dev server.

    ``path`` is the bit after ``/static/``. The view rebuilds the full Vite URL
    as ``http://<host>:<port>/static/<path>`` using django-vite's configured
    host/port + any query string, fetches it with a short timeout (the dev
    server is local), and streams the response back with content-type and body
    intact. Hop-by-hop + client-only headers are stripped; everything else
    passes through unchanged.

    Returns Vite's status (incl. 404 for genuinely-missing files) or ``502``
    if Vite is unreachable (the dev server isn't running — a configuration
    error worth surfacing, not hiding).
    """
    vite_cfg = getattr(settings, "DJANGO_VITE", {}).get("default", {})
    vite_host = vite_cfg.get("dev_server_host", "localhost")
    vite_port = vite_cfg.get("dev_server_port", 5173)
    query = request.META.get("QUERY_STRING", "")
    upstream_url = f"http://{vite_host}:{vite_port}/static/{path}"
    if query:
        upstream_url += f"?{query}"

    # Forward client headers (minus hop-by-hop + client-only) so Vite sees the
    # original Accept / If-None-Match etc. — its content negotiation + HMR
    # cache validation depend on them.
    forwarded_headers: dict[str, str] = {}
    for key, value in request.headers.items():
        if key.lower() in _HOP_BY_HOP or key.lower() in _CLIENT_ONLY:
            continue
        forwarded_headers[key] = value

    upstream_req = urllib.request.Request(
        upstream_url, headers=forwarded_headers, method="GET"
    )
    try:
        with urllib.request.urlopen(upstream_req, timeout=10) as upstream_resp:
            body = upstream_resp.read()
            response = HttpResponse(
                body, status=upstream_resp.status, reason=upstream_resp.reason
            )
            for key, value in upstream_resp.headers.items():
                if key.lower() in _HOP_BY_HOP:
                    continue
                response[key] = value
            return response
    except urllib.error.HTTPError as exc:
        # Vite responded (just not 200) — pass its status + body through so the
        # browser sees the real failure (e.g. a genuine 404 for a typo'd path).
        body = exc.read() if hasattr(exc, "read") else b""
        response: HttpResponse = HttpResponse(body, status=exc.code)
        for key, value in (exc.headers or {}).items():
            if key.lower() in _HOP_BY_HOP:
                continue
            response[key] = value
        return response
    except urllib.error.URLError as exc:
        # Vite is unreachable — the dev server isn't running. Surface it as a
        # 502 (bad gateway) rather than hiding behind a 404, so the developer
        # sees "Vite is down" not "asset is missing". Django 6.0 has no
        # ``HttpResponseBadGateway``; build the response with the explicit code.
        return HttpResponse(
            f"Dev Vite proxy could not reach {upstream_url}: {exc.reason}",
            status=502,
        )


class ViteProxyStaticFilesHandler(StaticFilesHandler):
    """A ``StaticFilesHandler`` that proxies staticfiles misses to Vite.

    Same claim rule as the parent (any ``/static/...`` path) so it fully
    replaces the parent in the dev runserver. On a path the staticfiles
    finders can resolve (real files under ``STATICFILES_DIRS`` / apps), behave
    identically to the parent. On a miss (the path lives only in Vite's module
    graph), forward to Vite instead of returning 404.

    This is the single seam that makes Vite-served assets resolve when the
    browser loads the SPA from Django's origin (``:8000``) — the document
    origin vs. dev-server origin mismatch that broke asset imports in dev.
    """

    def serve(self, request: HttpRequest) -> HttpResponse:
        try:
            return super().serve(request)
        except Exception:
            # The parent raised (almost always Http404 from a finder miss).
            # Strip the leading ``STATIC_URL`` segment from the path and proxy
            # the rest to Vite. ``request.path`` is the full path including the
            # ``/static/`` prefix; we forward only what follows it.
            static_prefix = settings.STATIC_URL or "static/"
            path = request.path
            if path.startswith("/") and static_prefix.startswith("/"):
                # Both rooted — compare on the unrooted form.
                path = path.lstrip("/")
                prefix = static_prefix.lstrip("/")
            else:
                prefix = static_prefix
            if path.startswith(prefix):
                path = path[len(prefix):]
            return _proxy_to_vite(request, path)

