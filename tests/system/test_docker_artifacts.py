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
