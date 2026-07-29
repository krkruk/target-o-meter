# Target-o-meter Docker image (dev + prod).
#
# Two stages share a common Python 3.14 base with the opencv system deps the
# vision domain needs (per infrastructure.md Risk Register: pre-build opencv
# into the image so Railpack/Docker don't rebuild it on every change):
#   - ``dev``: deps + the baked frontend bundle (``npm ci && npm run build``).
#     Backend Python ``src/`` is bind-mounted at runtime (compose) for live-
#     reload, but ``src/frontend/`` is NOT mounted (the dev compose mounts only
#     the backend packages), so the baked ``dist/`` survives. The dev compose
#     runs DEBUG=True + DJANGO_VITE_DEV_MODE=False so the bundle is served via
#     manifest mode (no Vite dev server in-container). HMR stays on native
#     ``make dev``.
#   - ``prod``: copies ``src/`` and builds the frontend so ``collectstatic`` has
#     the hashed bundle, then runs under gunicorn (NOT runserver).
#
# The dev compose (docker-compose.dev.yml) targets ``dev``; the prod compose
# (docker-compose.prod.yml) targets ``prod``. Entrypoint is deferred to compose
# (dev-seed.sh wraps migrate + seed + runserver/qcluster in dev; prod runs
# gunicorn directly).

ARG PYTHON_VERSION=3.14-slim

# ---------------------------------------------------------------------------
# Base: Python + opencv system deps + uv. Shared by dev and prod.
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS base

# opencv-python-headless wheel deps on slim (libGL + glib). ``gl1`` is the
# GL vendor-neutral runtime; ``glib2.0-0`` is glib. ``libglib2.0-0`` is the
# Debian name; bookworm ships it under that name.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        libgomp1 \
        # gunicorn is the prod server (prod stage runs it).
        # ``file`` + ``curl`` are convenience for in-container debugging.
        curl \
        file \
    && rm -rf /var/lib/apt/lists/*

# Copy uv from the official copyuv image (pinned by tag, not ``:latest``).
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/

WORKDIR /app

# Install deps once (Docker cache: deps change far less often than src/).
# ``--frozen`` refuses to write the lockfile — CI-grade reproducibility.
#
# All runtime deps live in [project.dependencies] (django, django-ninja,
# django-q2, authlib, django-vite, the CV/scipy stack, gunicorn, whitenoise).
# ``uv sync`` installs [project.dependencies] by default; ``--no-dev`` excludes
# the dev/test/system-test groups (ruff, pytest, playwright — not runtime).
# The result is the full runtime dep set the container needs. (Previously
# runtime deps were split into a ``default`` group + [project.dependencies];
# that broke Railpack, whose default sync skips groups. Runtime now lives only
# in [project.dependencies].)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# Dev stage: deps + the baked frontend bundle. Backend Python src/ is bind-
# mounted at runtime (compose), but ``src/frontend/`` is NOT — the dev compose
# mounts only the backend Python packages + manage.py (not ``./src/frontend``)
# so this baked ``dist/`` survives as the container's frontend source.
# ---------------------------------------------------------------------------
FROM base AS dev
# Bake the frontend bundle so the dev container serves the SPA without a Vite
# dev server. ``docker-compose.dev.yml`` runs DEBUG=True (backend dev) +
# DJANGO_VITE_DEV_MODE=False so django-vite serves this bundle from
# ``dist/manifest.json`` (manifest mode) instead of the absent :5173 dev server.
# Native ``make dev`` (Django :8000 + Vite :5173 HMR) is unchanged — that path
# doesn't use the image. Mirrors the prod stage's build; scoped to src/frontend.
COPY src/frontend ./src/frontend
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/* \
    && cd src/frontend \
    && npm ci \
    && npm run build \
    && rm -rf node_modules
CMD ["uv", "run", "python", "src/manage.py", "runserver", "0.0.0.0:8000"]

# ---------------------------------------------------------------------------
# Prod stage: copy src/, build the frontend, collect hashed static assets.
# ---------------------------------------------------------------------------
FROM base AS prod

# Copy the application + frontend source + the project-level templates dir.
# (settings.py's TEMPLATES['DIRS'] points at BASE_DIR.parent / 'templates' —
# BASE_DIR is /app/src in the container, so templates live at /app/templates/.
# The dev stage bind-mounts this; the prod stage must COPY it.)
COPY src/ ./src/
COPY templates/ ./templates/

# Build the frontend bundle so collectstatic has the hashed assets to serve
# via WhiteNoise. Node is not in the base image; install it via the official
# image's bundled node (bookworm slim has no node by default). We use the
# NodeSource Debian repo pinned to a major LTS line.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/* \
    && cd src/frontend \
    && npm ci \
    && npm run build \
    && rm -rf node_modules

ENV DEBUG=False
# collectstatic reads STATIC_ROOT; the prod compose mounts a volume there so the
# hashed bundle persists. Building it at image-build time also lets the image
# boot standalone (no volume) — WhiteNoise serves from the baked copy.
RUN uv run python src/manage.py collectstatic --noinput --clear

# gunicorn is a default-group dep (pyproject.toml). The prod compose runs it.
# The module path is ``src.target_o_meter.wsgi`` (the repo's DDD layout puts the
# Django project under src/; PYTHONPATH=/app makes ``src`` importable but not
# ``target_o_meter`` directly).
CMD ["uv", "run", "gunicorn", "src.target_o_meter.wsgi:application", "--bind", "0.0.0.0:8000"]
