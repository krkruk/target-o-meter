"""Shared fixtures for the system test suite.

System tests (AGENTS.md §4) exercise cross-domain API + integration behavior.
They go through ``test_utils.py`` or the REST API (AGENTS.md §5 — never ORM
tools like factory_boy directly against domain models).

The blackbox harness (S-01) lives here too: ``runserver`` / ``runserver_factory``
boot a real ``manage.py runserver`` subprocess on an ephemeral port, against a
fresh SQLite DB under ``results/<run-id>/``. Artifacts are kept on test failure
for post-mortem (the captured ``runserver.stderr`` is the primary runtime-error
oracle) and cleaned on success. See ``.agents/skills/system-test/`` for the
rationale and the V-Model system-test procedure.
"""
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


@pytest.fixture
def owner_sub(monkeypatch: pytest.MonkeyPatch) -> str:
    """The canonical Owner sub for system tests. Pinned via monkeypatch so it
    tears down cleanly per-test."""
    sub = "auth0|sys-owner-sub"
    monkeypatch.setenv("OWNER_SUB_ID", sub)
    return sub


@pytest.fixture
def user_sub() -> str:
    """A plain (non-Owner) sub for system tests."""
    return "auth0|sys-user-sub"


# ---------------------------------------------------------------------------
# Live-server fixture (S-01): spawn a real ``manage.py runserver`` subprocess.
# ---------------------------------------------------------------------------
#
# The rest of the system suite drives the BFF via the Django test client
# (``client.force_login``), which is fast but never exercises the real
# request-serving stack (the OIDC redirect chain, the dev-bypass middleware on
# a live ``request.user``, the actual ``/v1/...`` URL routing through WSGI).
# Some bugs only surface on that path — e.g. the dev-bypass nick collision
# (S-01), which raises ``IntegrityError`` on a real ``get_or_create`` INSERT.
#
# Clean-context contract: every boot writes its DB + captured stderr + any seed
# outputs under ``results/<run-id>/`` (NOT pytest's tmp_path, which auto-deletes
# and would discard the post-mortem artifacts exactly when a test fails). On a
# PASSED test the run dir is removed (keeps ``results/`` tidy); on a FAILED one
# it is left in place so the developer can read ``runserver.stderr`` and the DB
# file. The outcome is threaded in via the ``_system_test_outcomes`` stash +
# ``pytest_runtest_makereport`` hook below.
#
# ``--noreload`` keeps the autoreloader from forking, so there is exactly one
# PID to terminate. ``start_new_session=True`` lets teardown kill the whole
# process group (the server may spawn children).

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANAGE_PY = _REPO_ROOT / "src" / "manage.py"
# Repo-relative clean-context root. Gitignored (see .gitignore). One subdir per
# boot, named by the test + a short UUID so parallel runs never collide.
_RESULTS_ROOT = _REPO_ROOT / "results"

# Env vars that must NOT leak from the developer's shell / ``.env`` into the
# spawned ``runserver`` / CLI subprocess. Without this denylist, a ``.env`` on
# disk (loaded into ``os.environ`` by ``settings.load_dotenv()`` during any
# earlier ``manage.py`` invocation in the same process tree) silently flips
# test outcomes — e.g. ``DEV_AUTH_BYPASS_SUB`` activates the dev-auth-bypass
# middleware, turning the expected 401 on ``/v1/me`` into a 200 and breaking
# 6 live-server tests. Tests that need a specific value pass it via the
# fixture's ``extra_env=`` argument, which is applied AFTER the sanitize step
# and so overrides the denylist cleanly. (S-02 impl-review F10.)
_SANITIZED_ENV_DENYLIST = frozenset({
    # Dev-only auth bypass + dev admin seeding.
    "DEV_AUTH_BYPASS_SUB",
    "DEV_ADMIN_SUB",
    "DEV_ADMIN_NICK",
    "DEV_ADMIN_PASSWORD",
    # Real credentials — tests must opt in via extra_env, never inherit.
    "GOOGLE_API_KEY",
    "AUTH0_CLIENT_ID",
    "AUTH0_CLIENT_SECRET",
    "AUTH0_DOMAIN",
    "AUTH0_SECRET",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_STORAGE_BUCKET_NAME",
    "AWS_S3_ENDPOINT_URL",
    "AWS_S3_ADDRESSING_STYLE",
    # Owner identity — tests set this via the ``owner_sub`` fixture / extra_env.
    "OWNER_SUB_ID",
    # Backend selectors — tests that exercise a specific branch pass extra_env.
    "USE_S3",
    "VISION_DETECTOR",
    "OLLAMA_HOST",
    "OLLAMA_MODEL",
    # Redirected to an empty file under run_dir by ``_sanitized_env`` so the
    # subprocess's own ``load_dotenv()`` can't re-introduce the leak.
    "TOM_ENV_FILE",
})


def _sanitized_env(run_dir: Path) -> dict[str, str]:
    """``os.environ`` with the denylist above stripped AND ``TOM_ENV_FILE``
    pointed at an empty file under ``run_dir``.

    The ``TOM_ENV_FILE`` redirect is the load-bearing part: ``settings.py``
    calls ``load_dotenv()`` at import time, and the no-arg form walks up from
    the settings file to find the repo-root ``.env``. Without redirecting it,
    the spawned subprocess re-reads the developer's ``.env`` *itself* and
    re-introduces every var the denylist just stripped — so the denylist alone
    is necessary but not sufficient. Pointing ``TOM_ENV_FILE`` at an empty
    file makes ``load_dotenv`` a clean no-op (returns True on an empty file,
    no warning, no env pollution).

    ``extra_env=`` overrides land on top of this and so win cleanly.
    """
    env = {
        k: v for k, v in os.environ.items()
        if k not in _SANITIZED_ENV_DENYLIST
    }
    # Empty .env in the run-dir → load_dotenv reads nothing, returns True.
    (run_dir / ".env").touch()
    env["TOM_ENV_FILE"] = str(run_dir / ".env")
    return env


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
        # Set by the makereport hook so teardown knows keep-vs-clean.
        self.test_failed = False
        self._client = httpx.Client(base_url=f"http://{addr}", timeout=5.0)
        self._stderr_fp = None

    # -- HTTP convenience --------------------------------------------------
    def get(self, path: str, **kw):
        return self._client.get(path, **kw)

    def post(self, path: str, **kw):
        return self._client.post(path, **kw)

    def patch(self, path: str, **kw):
        return self._client.patch(path, **kw)

    # -- Diagnostics -------------------------------------------------------
    @property
    def stderr(self) -> str:
        """Server's captured stderr so far (tracebacks land here)."""
        try:
            return self.stderr_path.read_text(errors="replace")
        except OSError:
            return ""

    def assert_no_traceback(self) -> None:
        """Fail if the server emitted a Python traceback (a 500 in dev)."""
        log = self.stderr
        if "Traceback (most recent call last)" in log:
            raise AssertionError(
                f"server traceback detected in runserver stderr:\n{log}"
            )

    def assert_traceback(self, *, containing: str = "") -> None:
        """Assert the server DID emit a traceback (for bug-repro tests)."""
        log = self.stderr
        assert "Traceback (most recent call last)" in log, (
            f"expected a server traceback but found none.\nstderr:\n{log}"
        )
        if containing:
            assert containing in log, (
                f"expected {containing!r} in the traceback.\nstderr:\n{log}"
            )


def _free_port() -> int:
    """Allocate an ephemeral port the OS guarantees is free at call time."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _boot_runserver(
    run_dir: Path,
    *,
    extra_env: dict[str, str] | None = None,
    seed_script: str | None = None,
) -> LiveServer:
    """Migrate a throwaway DB under ``run_dir``, optionally seed, then boot.

    Shared by the public ``runserver`` fixture (no seed) and tests that need a
    pre-boot seed (the dev-bypass nick-collision repro: the colliding row must
    exist BEFORE the server reads ``DEV_AUTH_BYPASS_SUB`` on first request).
    """
    port = _free_port()
    addr = f"127.0.0.1:{port}"
    db_path = run_dir / "liveserver.sqlite3"
    static_root = run_dir / "staticfiles"
    stderr_file = run_dir / "runserver.stderr"

    # Throwaway DB isolated from the developer's ``src/db.sqlite3``. settings
    # reads the path from RAILWAY_VOLUME_MOUNT_PATH (falling back to BASE_DIR);
    # pointing it at ``run_dir`` makes migrate / seed / server all agree.
    # STATIC_ROOT is pointed at a run-dir subdir for the same reason: prod-mode
    # tests run ``collectstatic`` here so the hashed bundle is served (Phase 5.C
    # blank-page bug fix) without colliding with parallel boots or polluting the
    # developer's ``src/staticfiles``.
    base_env = {
        **_sanitized_env(run_dir),
        "RAILWAY_VOLUME_MOUNT_PATH": str(run_dir),
        "STATIC_ROOT": str(static_root),
        "DJANGO_SETTINGS_MODULE": "src.target_o_meter.settings",
        **(extra_env or {}),
    }

    def _run_manage(args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_MANAGE_PY), *args],
            cwd=str(_REPO_ROOT / "src"),
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

    # Phase 5.C: collect the hashed bundle into STATIC_ROOT so WhiteNoise can
    # serve it in DEBUG=False. Harmless in dev (django-vite proxies to the Vite
    # dev server instead), load-bearing in prod. Skipped silently when no
    # frontend build exists (``src/frontend/dist``) so non-frontend tests don't
    # depend on a build artifact.
    collectstatic_proc = _run_manage(["collectstatic", "--noinput", "--clear"])
    if collectstatic_proc.returncode != 0:
        raise RuntimeError(
            f"collectstatic failed (rc={collectstatic_proc.returncode}):\n"
            f"stdout:\n{collectstatic_proc.stdout}\n"
            f"stderr:\n{collectstatic_proc.stderr}"
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
        start_new_session=True,  # kill the whole group on teardown
    )

    server = LiveServer(
        addr=addr, proc=proc, run_dir=run_dir, stderr=stderr_file,
        db_path=db_path, base_env=base_env,
    )
    server._stderr_fp = stderr_fp

    # Readiness poll: hit /v1/me until we get any HTTP response, or the server
    # dies, or we time out. We do NOT fail on a traceback here — a bug repro
    # may legitimately 500 on the readiness hit, and the test asserts on that.
    deadline = time.monotonic() + 30.0
    ready = False
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(
                f"runserver exited early (rc={proc.returncode}):\n{server.stderr}"
            )
        try:
            server.get("/v1/me")
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
    """Kill the subprocess; clean the run dir unless the test failed.

    On failure, leave ``server.run_dir`` (DB + ``runserver.stderr``) in place
    under ``results/`` for post-mortem — the captured log is the primary
    runtime-error oracle when debugging a red system test.
    """
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
        # Surface the kept path so the failure trace points at the artifacts.
        print(f"\n[system-test] kept failed-run artifacts at: {server.run_dir}")


@pytest.fixture
def runserver(request: pytest.FixtureRequest) -> LiveServer:
    """Boot a real ``runserver --noreload`` on an ephemeral port (clean DB).

    Artifacts land under ``results/<test>-<uuid>/``. The child inherits the
    caller's env, so ``monkeypatch.setenv`` on ``DEV_AUTH_BYPASS_SUB`` /
    ``OWNER_SUB_ID`` set BEFORE this fixture runs reaches the server. For tests
    that need to flip env mid-session or pre-seed the DB, use
    ``runserver_factory`` instead.

    Named ``runserver`` (not ``live_server``) to avoid clashing with
    pytest-django's built-in ``live_server`` fixture.
    """
    server = _boot_runserver(_run_dir_for(request.node.name))
    request.node._live_server = server  # makereport hook reads this
    try:
        yield server
    finally:
        _teardown_runserver(server)


@pytest.fixture
def runserver_factory(request: pytest.FixtureRequest, tmp_path: Path):
    """Factory: boot a ``runserver`` with caller-controlled env + optional seed.

    Yields a callable ``boot(*, extra_env=None, seed_script=None) -> LiveServer``
    and tears down whatever it booted. Use this for tests that need a specific
    boot-time env (e.g. a pre-set ``DEV_AUTH_BYPASS_SUB`` whose derived nick
    collides with a seeded row) — env flipped via ``monkeypatch.setenv`` AFTER
    boot does NOT reach the already-running server's module-level cache.

    Each ``boot()`` call gets its own ephemeral port + fresh run dir under
    ``results/`` so multiple boots in one test don't collide.
    """
    booted: list[LiveServer] = []

    def boot(
        *, extra_env: dict[str, str] | None = None, seed_script: str | None = None,
    ) -> LiveServer:
        run_dir = _run_dir_for(f"{request.node.name}-boot{len(booted)}")
        server = _boot_runserver(
            run_dir, extra_env=extra_env, seed_script=seed_script,
        )
        booted.append(server)
        return server

    # Let the makereport hook see every booted server (for keep-on-failure).
    request.node._live_servers = booted
    try:
        yield boot
    finally:
        for server in booted:
            _teardown_runserver(server)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    """Thread the test outcome onto each ``LiveServer`` / ``CliResult`` so
    teardown can decide keep-on-failure vs clean-on-success.

    ``makereport`` runs for setup/call/teardown phases; we only care about the
    ``call`` phase (the test body). If it did not pass, flag every server / CLI
    result attached to the node so teardown keeps the run dir.
    """
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        for attr in ("_live_server", "_live_servers", "_cli_results"):
            val = getattr(item, attr, None)
            if val is None:
                continue
            servers = val if isinstance(val, list) else [val]
            for server in servers:
                server.test_failed = True


# ---------------------------------------------------------------------------
# CLI fixture (S-02): one subprocess per invocation, isolated under results/.
# ---------------------------------------------------------------------------
#
# The sibling of ``runserver`` for one-shot CLI commands (manage.py shell, the
# vision CLI). Each call gets its own ``results/<test>-<uuid>/`` as cwd AND as
# the Django DB dir (``RAILWAY_VOLUME_MOUNT_PATH`` points there, mirroring the
# runserver clean-context contract — never the developer's ``src/db.sqlite3``).
# Captures stdout + stderr + exit code; keeps the run dir on failure so the
# captured stderr is the post-mortem oracle.


class CliResult:
    """Outcome of one CLI invocation — the three observable signals."""

    def __init__(self, proc: subprocess.CompletedProcess, run_dir: Path) -> None:
        self.returncode = proc.returncode
        self.stdout = proc.stdout
        self.stderr = proc.stderr
        self.run_dir = run_dir
        self.test_failed = False  # set by the makereport hook

    def assert_no_traceback(self) -> None:
        """Fail if stderr contains a Python traceback (a swallowed exception)."""
        if b"Traceback (most recent call last)" in self.stderr:
            raise AssertionError(
                f"CLI traceback in stderr:\n{self.stderr.decode(errors='replace')}"
            )

    def assert_success(self) -> None:
        """rc == 0 AND no traceback. Pair with a stdout/output-file assertion."""
        assert self.returncode == 0, (
            f"expected exit 0, got {self.returncode}\n"
            f"stdout:\n{self.stdout.decode(errors='replace')}\n"
            f"stderr:\n{self.stderr.decode(errors='replace')}"
        )
        self.assert_no_traceback()


@pytest.fixture
def cli(request: pytest.FixtureRequest):
    """Factory: run a CLI command in a clean run dir under ``results/``.

    Yields ``run(argv, *, extra_env=None, cwd=None) -> CliResult``. Each call
    gets its own ``results/<test>-<uuid>/`` as cwd and as the Django DB dir
    (``RAILWAY_VOLUME_MOUNT_PATH``). Like the server fixture, failed runs keep
    their run dir for post-mortem; successful runs clean up.
    """
    runs: list[CliResult] = []

    def run(
        argv: list[str],
        *,
        extra_env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> CliResult:
        run_dir = _run_dir_for(f"{request.node.name}-run{len(runs)}")
        env = {
            **_sanitized_env(run_dir),
            "RAILWAY_VOLUME_MOUNT_PATH": str(run_dir),
            **(extra_env or {}),
        }
        proc = subprocess.run(
            argv,
            cwd=str(cwd or run_dir),
            env=env,
            capture_output=True,
            timeout=180,
        )
        result = CliResult(proc, run_dir)
        runs.append(result)
        return result

    request.node._cli_results = runs  # makereport hook reads this
    try:
        yield run
    finally:
        for r in runs:
            if not r.test_failed:
                shutil.rmtree(r.run_dir, ignore_errors=True)
            else:
                print(f"\n[system-test] kept failed-run artifacts at: {r.run_dir}")
