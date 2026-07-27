# Target-o-meter Docker image (dev + prod).
#
# Two stages share a common Python 3.14 base with the opencv system deps the
# vision domain needs (per infrastructure.md Risk Register: pre-build opencv
# into the image so Railpack/Docker don't rebuild it on every change):
#   - ``dev``: deps only — ``src/`` is bind-mounted at runtime for live-reload,
#     so it is NOT copied here (keeps the build cache stable across code edits).
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
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# Dev stage: deps only. src/ is bind-mounted at runtime (compose).
# ---------------------------------------------------------------------------
FROM base AS dev
# Intent: live-reload. Copying src/ here would be overwritten by the bind mount
# AND bust the cache on every code edit, so it is intentionally absent.
CMD ["uv", "run", "python", "src/manage.py", "runserver", "0.0.0.0:8000"]

# ---------------------------------------------------------------------------
# Prod stage: copy src/, build the frontend, collect hashed static assets.
# ---------------------------------------------------------------------------
FROM base AS prod

# Copy the application + frontend source.
COPY src/ ./src/

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
CMD ["uv", "run", "gunicorn", "target_o_meter.wsgi:application", "--bind", "0.0.0.0:8000"]
