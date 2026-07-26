# Live-server harness (reference)

Load this when you need to build or adapt the blackbox server fixture. It is the full, real source of the `runserver` / `runserver_factory` fixtures from `tests/system/conftest.py`, annotated with **why each line exists** — the gotchas the harness encodes are not obvious from reading the code.

The goal: boot a real `manage.py runserver` as a subprocess on an ephemeral port, against a throwaway SQLite DB, with captured stderr, and tear it down cleanly. This is what makes a blackbox system test possible.

## Design decisions (and the gotcha each one absorbs)

| Decision | The gotcha it absorbs |
|---|---|
| Artifacts under `results/<run-id>/`, NOT pytest `tmp_path` | `tmp_path` auto-deletes on teardown — discarding the post-mortem `runserver.stderr` and DB exactly when a test fails. `results/` keeps failed-run artifacts for debugging; success cleans up. |
| `--noreload` on the runserver command | The autoreloader forks; without this you have two PIDs and a dangling child on teardown. |
| Bind to port `0` → read assigned port back | Hardcoded `:8000` collides with parallel runs / CI matrices (`EADDRINUSE`). |
| `start_new_session=True` + `os.killpg` on teardown | The server may spawn children (e.g. the reloader); killing only the parent leaks them. |
| Capture stdout+stderr to a file under `results/<run-id>/` | The captured log is the primary runtime-error oracle — `assert_no_traceback()` reads it. Streaming to a pipe deadlocks on large output; a file does not. |
| Poll `GET /v1/me` for readiness before driving | The subprocess launching is not the server being ready; driving too early gets `ConnectionError`. |
| DB path via the app's DB-path env var, pointed at `results/<run-id>/` | The app reads its DB path from this var; pointing it at the run dir gives every subprocess (migrate, seed, server) the same throwaway DB. Never the dev's primary DB. |
| Fixture named `runserver`, NOT `live_server` | pytest-django ships a `live_server` fixture; the name clash raises `AttributeError: '_live_server_modified_settings'`. |
| Two fixtures: `runserver` (clean DB, no seed) + `runserver_factory` (caller-controlled env + optional pre-boot seed) | Some bugs (the nick collision) need a row to exist *before* the server reads env on first request — env flipped after boot doesn't reach the server's module-level cache. |
| `pytest_runtest_makereport` hook threads test outcome → `server.test_failed` | Lets teardown decide keep-on-failure vs clean-on-success without the test itself having to remember. |

## The fixtures (copy this shape — do not reinvent)

This is the real, working source. **Values marked `# repo-specific — adapt`** are specific to this repo (target-o-meter). When copying to a different app, change them — see "Adapting to a non-Django app" below for what each maps to.

```python
"""Shared fixtures for the system test suite — incl. the live-server harness."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
# repo-specific — adapt: where manage.py lives relative to repo root.
_MANAGE_PY = _REPO_ROOT / "src" / "manage.py"
# Clean-context root (gitignored). One subdir per boot.
_RESULTS_ROOT = _REPO_ROOT / "results"


def _run_dir_for(test_id: str) -> Path:
    """Allocate a unique ``results/<test-id>-<uuid8>/`` for one boot."""
    run_id = f"{test_id}-{uuid.uuid4().hex[:8]}"
    run_dir = _RESULTS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


class LiveServer:
    """Handle to a running ``runserver`` subprocess + captured stderr log."""

    def __init__(
        self, *, addr: str, proc: subprocess.Popen, run_dir: Path,
        stderr: Path, db_path: Path, base_env: dict[str, str],
    ) -> None:
        self.addr = addr
        self.proc = proc
        self.run_dir = run_dir
        self.stderr_path = stderr
        self.db_path = db_path
        self.base_env = base_env
        self.test_failed = False  # set by the makereport hook
        self._client = httpx.Client(base_url=f"http://{addr}", timeout=5.0)
        self._stderr_fp = None

    # -- HTTP convenience --------------------------------------------------
    def get(self, path: str, **kw):  return self._client.get(path, **kw)
    def post(self, path: str, **kw): return self._client.post(path, **kw)
    def patch(self, path: str, **kw): return self._client.patch(path, **kw)

    # -- Diagnostics -------------------------------------------------------
    @property
    def stderr(self) -> str:
        try:
            return self.stderr_path.read_text(errors="replace")
        except OSError:
            return ""

    def assert_no_traceback(self) -> None:
        log = self.stderr
        if "Traceback (most recent call last)" in log:
            raise AssertionError(f"server traceback in stderr:\n{log}")

    def assert_traceback(self, *, containing: str = "") -> None:
        log = self.stderr
        assert "Traceback (most recent call last)" in log, (
            f"expected a server traceback but found none.\nstderr:\n{log}"
        )
        if containing:
            assert containing in log, (
                f"expected {containing!r} in the traceback.\nstderr:\n{log}"
            )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _boot_runserver(
    run_dir: Path,
    *,
    extra_env: dict[str, str] | None = None,
    seed_script: str | None = None,
) -> LiveServer:
    port = _free_port()
    addr = f"127.0.0.1:{port}"
    db_path = run_dir / "liveserver.sqlite3"
    stderr_file = run_dir / "runserver.stderr"

    base_env = {
        **os.environ,
        # repo-specific — adapt: the env var YOUR app reads its DB path from.
        "RAILWAY_VOLUME_MOUNT_PATH": str(run_dir),
        # repo-specific — adapt: your Django settings module dotted path.
        "DJANGO_SETTINGS_MODULE": "src.target_o_meter.settings",
        **(extra_env or {}),
    }

    def _run_manage(args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_MANAGE_PY), *args],
            cwd=str(_REPO_ROOT / "src"),  # repo-specific — adapt: cwd for manage.py
            env=base_env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    migrate_proc = _run_manage(["migrate"])
    if migrate_proc.returncode != 0:
        raise RuntimeError(
            f"migrate failed (rc={migrate_proc.returncode}):\n"
            f"stdout:\n{migrate_proc.stdout}\nstderr:\n{migrate_proc.stderr}"
        )

    if seed_script:
        seed_proc = _run_manage(["shell", "-c", seed_script])
        if seed_proc.returncode != 0:
            raise RuntimeError(
                f"seed script failed (rc={seed_proc.returncode}):\n"
                f"stdout:\n{seed_proc.stdout}\nstderr:\n{seed_proc.stderr}"
            )

    stderr_fp = stderr_file.open("w")
    proc = subprocess.Popen(
        [sys.executable, str(_MANAGE_PY), "runserver", addr, "--noreload"],
        cwd=str(_REPO_ROOT / "src"),
        env=base_env,
        stdout=stderr_fp,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    server = LiveServer(
        addr=addr, proc=proc, run_dir=run_dir, stderr=stderr_file,
        db_path=db_path, base_env=base_env,
    )
    server._stderr_fp = stderr_fp

    # Readiness poll: hit a known endpoint until we get any HTTP response, the
    # server dies, or we time out. We do NOT fail on a traceback here — a bug
    # repro may legitimately 500 on the readiness hit.
    deadline = time.monotonic() + 30.0
    ready = False
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(
                f"runserver exited early (rc={proc.returncode}):\n{server.stderr}"
            )
        try:
            server.get("/v1/me")  # repo-specific — adapt: a cheap endpoint
            ready = True
            break
        except httpx.HTTPError:
            time.sleep(0.2)

    if not ready:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise AssertionError(
            f"runserver did not become ready within 30s:\n{server.stderr}"
        )

    return server


def _teardown_runserver(server: LiveServer) -> None:
    """Kill the subprocess; clean the run dir unless the test failed."""
    server._client.close()
    try:
        server.proc.terminate()
        server.proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.proc.kill()
    fp = getattr(server, "_stderr_fp", None)
    if fp:
        fp.close()
    if not server.test_failed:
        shutil.rmtree(server.run_dir, ignore_errors=True)
    else:
        print(f"\n[system-test] kept failed-run artifacts at: {server.run_dir}")


@pytest.fixture
def runserver(request: pytest.FixtureRequest) -> LiveServer:
    """Boot ``runserver --noreload`` on an ephemeral port (clean DB under results/)."""
    server = _boot_runserver(_run_dir_for(request.node.name))
    request.node._live_server = server  # makereport hook reads this
    try:
        yield server
    finally:
        _teardown_runserver(server)


@pytest.fixture
def runserver_factory(request: pytest.FixtureRequest):
    """Factory: boot with caller-controlled env + optional pre-boot seed.

    Yields ``boot(*, extra_env=None, seed_script=None) -> LiveServer``. Use this
    when a test needs a specific boot-time env (env flipped AFTER boot does NOT
    reach the running server's module-level cache).
    """
    booted: list[LiveServer] = []

    def boot(
        *, extra_env: dict[str, str] | None = None, seed_script: str | None = None,
    ) -> LiveServer:
        run_dir = _run_dir_for(f"{request.node.name}-boot{len(booted)}")
        server = _boot_runserver(run_dir, extra_env=extra_env, seed_script=seed_script)
        booted.append(server)
        return server

    request.node._live_servers = booted  # makereport hook reads this
    try:
        yield boot
    finally:
        for server in booted:
            _teardown_runserver(server)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    """Thread the test outcome onto each LiveServer so teardown can decide
    keep-on-failure vs clean-on-success."""
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        for attr in ("_live_server", "_live_servers"):
            val = getattr(item, attr, None)
            if val is None:
                continue
            servers = val if isinstance(val, list) else [val]
            for server in servers:
                server.test_failed = True
```

## Using the fixtures in a test

**Happy path** (clean DB, default env):
```python
def test_runserver_serves_v1_me_unauthenticated(runserver) -> None:
    response = runserver.get("/v1/me")
    assert response.status_code == 401
    runserver.assert_no_traceback()
```

**Boot-time env + pre-seed** (for collision/state bugs that need a row before first request):
```python
def test_dev_bypass_colliding_sub_does_not_500(runserver_factory) -> None:
    seed = (
        "from src.domains.identity.models import User; "
        "User.objects.create(sub='auth0|dev-bypass', nick='dev-auth0|de')"
    )
    server = runserver_factory(
        extra_env={"DEV_AUTH_BYPASS_SUB": "auth0|dev-beta"},
        seed_script=seed,
    )
    response = server.get("/v1/me")
    assert response.status_code == 200, response.text
    server.assert_no_traceback()
```

## Adapting to a non-Django app

The shape generalizes; swap the values marked `# repo-specific — adapt` above:
- **Launch command**: `gunicorn myapp:wsgi` / `npm run start` / `java -jar app.jar` instead of `manage.py runserver`.
- **DB path env var**: whatever your app reads (here `RAILWAY_VOLUME_MOUNT_PATH`). Find it in the settings/config, don't guess.
- **Migrate/seed command**: your app's own CLI (`prisma migrate deploy`, `alembic upgrade head`, `manage.py shell`) — go through the app's surface, never write the DB directly.
- **Readiness probe**: any cheap endpoint your app serves (`GET /health`, `GET /`). Poll until any HTTP status returns.
- **`--noreload` equivalent**: disable any file-watcher/auto-reload so the subprocess is a single PID.

If your test framework has no `pytest_runtest_makereport` equivalent for the keep-on-failure behavior, drop the hook and have each test set `server.test_failed = False` explicitly in a `finally` (default-keep is safer than default-clean if you can't detect the outcome).
