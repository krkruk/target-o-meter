"""System test: Phase 5 Docker artifacts (compose YAML, Dockerfile, dev-seed.sh).

Docker isn't available in every sandbox (and the daemon can't run inside CI
without a service), so the plan's ``docker compose config`` / ``docker build``
gates can't always execute. This test is the in-sandbox guard that pins what
CAN be verified without the daemon:

  - the dev + prod compose YAML parse and carry the documented service
    topology (web/worker/minio/create-bucket for dev; web/worker for prod)
  - the dev compose wires USE_S3=True + MinIO vars + VISION_DETECTOR=mock
    (the S3-against-MinIO + MockDetector dev posture from the plan)
  - the prod compose does NOT set DEV_AUTH_BYPASS_SUB (prod has no bypass)
  - the Makefile exposes ``dev-container`` + ``prod-container`` targets
  - ``.dockerignore`` excludes secrets (.env) + build artifacts
  - ``docker/dev-seed.sh`` is syntactically valid bash + is executable

The full bring-up (``make dev-container`` → healthy web/worker/minio, POST
``/v1/scoring/jobs`` lands a file in the MinIO bucket, prod stack serves the
SPA from the built bundle) stays as the manual gate where Docker runs.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_compose(name: str) -> dict:
    with (_REPO_ROOT / name).open() as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# docker-compose.dev.yml — service topology + dev posture
# ---------------------------------------------------------------------------


def test_dev_compose_has_documented_services() -> None:
    """Dev compose carries web + worker + minio + create-bucket (the four-
    service stack ``make dev-container`` brings up)."""
    doc = _load_compose("docker-compose.dev.yml")
    assert set(doc["services"]) == {
        "web", "worker", "minio", "create-bucket",
    }


def test_dev_compose_wires_s3_against_minio() -> None:
    """The dev compose flips USE_S3=True and points the S3 backend at MinIO —
    the S3-compatible dev parity with prod's Tigris-backed Storage Buckets."""
    doc = _load_compose("docker-compose.dev.yml")
    web_env = doc["services"]["web"]["environment"]
    assert web_env["USE_S3"] == "True"
    assert web_env["AWS_S3_ENDPOINT_URL"] == "http://minio:9000"
    assert web_env["AWS_S3_ADDRESSING_STYLE"] == "path"
    assert web_env["AWS_STORAGE_BUCKET_NAME"] == "target-o-meter-local"


def test_dev_compose_uses_mock_detector() -> None:
    """Dev compose sets VISION_DETECTOR=mock so the full round-trip runs
    without API keys (the S-02 dev posture)."""
    doc = _load_compose("docker-compose.dev.yml")
    for svc in ("web", "worker"):
        assert doc["services"][svc]["environment"]["VISION_DETECTOR"] == "mock"


def test_dev_compose_minio_has_healthcheck() -> None:
    """``web`` depends_on minio healthy, and minio exposes a healthcheck — so
    ``web`` doesn't boot before the S3 API answers."""
    doc = _load_compose("docker-compose.dev.yml")
    minio = doc["services"]["minio"]
    assert "healthcheck" in minio
    web_depends = doc["services"]["web"]["depends_on"]
    assert web_depends["minio"]["condition"] == "service_healthy"


def test_dev_compose_create_bucket_depends_on_minio_healthy() -> None:
    """The one-shot create-bucket service waits for minio healthy before
    running ``mc mb`` (otherwise it'd race the boot and fail)."""
    doc = _load_compose("docker-compose.dev.yml")
    cb = doc["services"]["create-bucket"]
    assert cb["depends_on"]["minio"]["condition"] == "service_healthy"


def test_dev_compose_web_runs_dev_seed_entrypoint() -> None:
    """``web`` + ``worker`` run the dev-seed.sh entrypoint (migrate + seed,
    then exec runserver / qcluster via $SERVICE_ROLE)."""
    doc = _load_compose("docker-compose.dev.yml")
    for svc in ("web", "worker"):
        assert "dev-seed.sh" in str(doc["services"][svc]["command"])


# ---------------------------------------------------------------------------
# docker-compose.prod.yml — prod-shape posture
# ---------------------------------------------------------------------------


def test_prod_compose_has_web_and_worker_only() -> None:
    """Prod stack is web + worker (no MinIO — prod uses Railway Storage
    Buckets / Tigris)."""
    doc = _load_compose("docker-compose.prod.yml")
    assert set(doc["services"]) == {"web", "worker"}


def test_prod_compose_runs_gunicorn() -> None:
    """Prod ``web`` runs gunicorn (NOT runserver) — the prod serving path."""
    doc = _load_compose("docker-compose.prod.yml")
    cmd = doc["services"]["web"]["command"]
    assert "gunicorn" in str(cmd)
    assert "runserver" not in str(cmd)


def test_prod_compose_debug_false_and_no_bypass() -> None:
    """Prod stack is DEBUG=False AND does NOT set DEV_AUTH_BYPASS_SUB (prod
    has no auth bypass; E001 refuses to boot if it's set under DEBUG=False)."""
    doc = _load_compose("docker-compose.prod.yml")
    web_env = doc["services"]["web"]["environment"]
    assert web_env["DEBUG"] == "False"
    assert "DEV_AUTH_BYPASS_SUB" not in web_env, (
        "prod compose must NOT set DEV_AUTH_BYPASS_SUB — E001 refuses to boot "
        "under DEBUG=False with the bypass set."
    )


def test_prod_compose_does_not_shadow_baked_staticfiles() -> None:
    """``web`` must NOT mount a volume over ``/app/src/staticfiles``.

    The Dockerfile prod stage runs ``collectstatic`` into ``/app/src/staticfiles``
    at image-build time (its comment explicitly says this lets the image boot
    standalone). A named volume mounted there is NOT auto-populated from the
    image on first attach under podman-compose; on subsequent boots it PERSISTS
    a STALE ``collectstatic`` output whose ``staticfiles.json`` manifest hashes
    drift from the freshly-built bundle + vite manifest baked into the image.
    django-vite resolves ``{% vite_asset %}`` against the current vite manifest
    (e.g. ``assets/main-D2I6y11G.js``), but WhiteNoise's
    ``CompressedManifestStaticFilesStorage`` looks the name up in the STALE
    volume manifest → ``ValueError: Missing staticfiles manifest entry for …``
    → HTTP 500 on ``GET /`` (the prod-stack bug). The fix is to drop the volume;
    the image's baked copy is the source of truth.
    """
    doc = _load_compose("docker-compose.prod.yml")
    web_volumes = doc["services"]["web"].get("volumes", [])
    shadowing = [v for v in web_volumes if v.split(":")[-1] == "/app/src/staticfiles"]
    assert not shadowing, (
        f"web mounts a volume over /app/src/staticfiles ({shadowing}), which "
        f"shadows the image's baked collectstatic output → stale manifest → "
        f"'Missing staticfiles manifest entry' HTTP 500 on GET /. Drop the "
        f"mount; the Dockerfile bakes the bundle at build time."
    )


# ---------------------------------------------------------------------------
# Makefile targets
# ---------------------------------------------------------------------------


def test_makefile_has_container_targets() -> None:
    """``make help`` lists dev-container + prod-container (the user-facing
    entrypoints). Pins the Makefile wiring so a regression that drops them
    fails here, not at run time."""
    result = subprocess.run(
        ["make", "help"], cwd=str(_REPO_ROOT),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "dev-container" in out, "dev-container target missing from make help"
    assert "prod-container" in out, "prod-container target missing from make help"


def test_makefile_dev_container_uses_dev_compose() -> None:
    """The dev-container target invokes ``docker compose -f
    docker-compose.dev.yml`` (and prod-container the prod file). Reading the
    Makefile (not running it) avoids needing Docker to verify the wiring."""
    makefile = (_REPO_ROOT / "Makefile").read_text()
    assert "docker-compose.dev.yml" in makefile
    assert "docker-compose.prod.yml" in makefile


# ---------------------------------------------------------------------------
# .dockerignore — secrets + build artifacts excluded
# ---------------------------------------------------------------------------


def test_dockerignore_excludes_env_and_artifacts() -> None:
    """The image must never bake secrets (.env) or stale build artifacts
    (node_modules, dist, the frozen cv/ sandbox)."""
    ignored = (_REPO_ROOT / ".dockerignore").read_text().splitlines()
    ignored_set = {ln.strip() for ln in ignored if ln.strip() and not ln.startswith("#")}
    must_ignore = {
        ".env",
        "src/frontend/node_modules/",
        "cv/",
        "results/",
        ".venv/",
    }
    missing = must_ignore - ignored_set
    assert not missing, f".dockerignore missing: {missing}"


# ---------------------------------------------------------------------------
# dev-seed.sh — valid bash + executable
# ---------------------------------------------------------------------------


def test_dev_seed_script_is_valid_bash_and_executable() -> None:
    """``docker/dev-seed.sh`` parses as bash and has the executable bit (the
    compose ``command`` execs it; a syntax error or missing +x would fail the
    container boot)."""
    script = _REPO_ROOT / "docker" / "dev-seed.sh"
    assert script.exists(), "docker/dev-seed.sh missing"
    # Syntax check without executing.
    result = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"dev-seed.sh syntax error:\n{result.stderr}"
    # Executable bit.
    assert script.stat().st_mode & 0o100, "dev-seed.sh is not executable (+x)"


def test_dev_seed_script_seeds_via_app_surface_not_orm() -> None:
    """The seed goes through identity's service surface (get_or_create_user_
    by_sub / create_superuser), not raw ORM inserts (AGENTS.md §5)."""
    script = (_REPO_ROOT / "docker" / "dev-seed.sh").read_text()
    assert "get_or_create_user_by_sub" in script
    assert "create_superuser" in script
    # migrate runs unconditionally (idempotent re-runs safe).
    assert "manage.py migrate" in script


# ---------------------------------------------------------------------------
# Dev-container frontend serving (S-02 impl-review F11)
# ---------------------------------------------------------------------------
#
# ``make dev-container`` serves a blank page if the dev container has no
# servable frontend bundle. The dev compose runs DEBUG=True (no Vite dev
# server), so django-vite must be flipped into manifest mode AND the dev image
# must bake the bundle. These static assertions pin that wiring without needing
# the Docker daemon — the live serving contract is covered by
# ``test_spa_pipeline.py::test_dev_container_*``.


def test_dev_compose_forces_django_vite_manifest_mode() -> None:
    """``web`` sets ``DJANGO_VITE_DEV_MODE=False`` so django-vite serves the
    baked bundle (manifest mode), not the absent :5173 Vite dev server.

    Regression guard for the blank ``make dev-container`` page: without this
    env, ``dev_mode`` defaults to ``DEBUG=True`` and the entry tag points at
    ``http://localhost:5173/...`` with nothing answering → ``#root`` stays
    empty.
    """
    doc = _load_compose("docker-compose.dev.yml")
    assert doc["services"]["web"]["environment"]["DJANGO_VITE_DEV_MODE"] == "False"


def test_dev_compose_web_does_not_shadow_frontend_bundle() -> None:
    """``web`` does NOT mount ``./src:/app/src`` (the blanket mount that would
    shadow the dev image's baked ``src/frontend/dist`` with the host's
    gitignored-empty dist/).

    The granular mounts (``./src/target_o_meter``, ``./src/bff``,
    ``./src/domains``, ``./src/manage.py``) preserve backend live-reload
    without touching the frontend bundle. ``worker`` keeps the blanket mount —
    it never serves the frontend, so shadowing ``src/frontend`` there is
    harmless.
    """
    doc = _load_compose("docker-compose.dev.yml")
    web_volumes = doc["services"]["web"]["volumes"]
    shadowing = [v for v in web_volumes if v.split(":")[0] == "./src"]
    assert not shadowing, (
        f"web mounts the blanket ./src path ({shadowing}), which would shadow "
        f"the dev image's baked src/frontend/dist → blank page. Use granular "
        f"backend mounts instead."
    )
    # Sanity: the granular backend mounts that preserve live-reload are present.
    web_srcs = {v.split(":")[0] for v in web_volumes}
    assert {
        "./src/target_o_meter", "./src/bff", "./src/domains",
    } <= web_srcs, (
        f"granular backend mounts missing from web volumes: {web_volumes}"
    )


def test_devfile_dev_stage_builds_frontend() -> None:
    """The Dockerfile ``dev`` stage builds the frontend (``npm run build``) so
    the dev image carries ``src/frontend/dist`` for manifest-mode serving.

    The dev image is the only source of the frontend bundle in the dev
    container (no Vite service, host's dist/ is gitignored-empty). Mirrors the
    prod stage's build. Reading the Dockerfile as text (not building) avoids
    needing the daemon — the full build is the manual gate.
    """
    dockerfile = (_REPO_ROOT / "Dockerfile").read_text()
    # Locate the dev stage body (FROM base AS dev ... up to the next FROM).
    dev_start = dockerfile.find("FROM base AS dev")
    assert dev_start != -1, "no 'FROM base AS dev' stage in Dockerfile"
    next_from = dockerfile.find("\nFROM", dev_start + 1)
    dev_stage = dockerfile[dev_start : next_from if next_from != -1 else len(dockerfile)]
    assert "npm run build" in dev_stage, (
        "the dev stage does not run 'npm run build' — the dev image would have "
        "no src/frontend/dist → django-vite can't serve the bundle → blank page"
    )
    assert "npm ci" in dev_stage, "dev stage must run 'npm ci' before 'npm run build'"
