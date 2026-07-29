"""Blackbox regression for django-q2 issue #66: ``qcluster`` crashed on Ctrl+C
with ``AttributeError: 'NoneType' object has no attribute 'set'`` (or
``...is_set``) inside ``Cluster`` — because ``stop_event``/``start_event`` were
nulled after the first stop and the unguarded ``sig_handler`` re-entered the
now-half-destroyed object. The racy null-state crash trips a different
attribute depending on exactly when the duplicate signal / interpreter atexit
fires, so this test asserts on the whole *failure class* (any Python traceback
+ non-zero exit), not one exact line.

The fix is the ``qcluster`` override at
``src/target_o_meter/management/commands/qcluster.py`` (guarded idempotent
signal handlers + lifecycle ownership). This test boots a real ``qcluster``
subprocess against a throwaway broker DB, sends it a single SIGINT (mirroring
Ctrl+C) and asserts it shuts down cleanly: exits within a few seconds (rc 0),
no traceback.

Separately, CPython's ``multiprocessing.resource_tracker`` emits a benign
"leaked semaphore objects to clean up at shutdown" ``UserWarning`` under the
``forkserver``/``Queue`` interaction (CPython #46391) — the tracker RECLAIMS
the semaphores, it just complains, and it fires even after a fully graceful
shutdown. That is **not** the #66 crash and is not caused by this project's
code. This test still flags it when present (best-effort) so a regression that
made it worse would surface, but its absence in a standalone run is
timing-dependent and not load-bearing for the crash fix.

This is the config/process class (signal handling + multiprocessing teardown),
not pure logic, so per the TDD-ability gate it lives in the blackbox system
suite. ``test_native_dev_qcluster.py`` covers *that qcluster runs and consumes
jobs*; this test covers *that it shuts down cleanly* — orthogonal concerns, so
they're separate files.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest


pytestmark = pytest.mark.dev


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANAGE_PY = _REPO_ROOT / "src" / "manage.py"
_RESULTS_ROOT = _REPO_ROOT / "results"


# Mirror the sanitized-env denylist from tests/system/conftest.py: we spawn a
# real subprocess, so it must NOT inherit the developer's .env (which could
# point at real S3 / Auth0 and would pollute the run). qcluster needs none of
# those vars — it only needs a migrated DB.
_SANITIZED_ENV_DENYLIST = frozenset({
    "DEV_AUTH_BYPASS_SUB", "DEV_ADMIN_SUB", "DEV_ADMIN_NICK", "DEV_ADMIN_PASSWORD",
    "GOOGLE_API_KEY",
    "AUTH0_CLIENT_ID", "AUTH0_CLIENT_SECRET", "AUTH0_DOMAIN", "AUTH0_SECRET",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_STORAGE_BUCKET_NAME",
    "AWS_S3_ENDPOINT_URL", "AWS_S3_ADDRESSING_STYLE",
    "OWNER_SUB_ID", "USE_S3", "VISION_DETECTOR", "OLLAMA_HOST", "OLLAMA_MODEL",
    "TOM_ENV_FILE",
})


def _sanitized_env(run_dir: Path) -> dict[str, str]:
    """os.environ with the denylist stripped + TOM_ENV_FILE pointed at an empty
    .env (same load_dotenv-neutralization trick as conftest.py)."""
    env = {
        k: v for k, v in os.environ.items()
        if k not in _SANITIZED_ENV_DENYLIST
    }
    (run_dir / ".env").touch()
    env["TOM_ENV_FILE"] = str(run_dir / ".env")
    return env


def _run_dir() -> Path:
    run_dir = _RESULTS_ROOT / f"qcluster-shutdown-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _migrate(run_dir: Path, base_env: dict[str, str]) -> None:
    """Migrate a throwaway DB under run_dir (qcluster needs the django-q ORM
    broker tables)."""
    proc = subprocess.run(
        [sys.executable, str(_MANAGE_PY), "migrate"],
        cwd=str(_MANAGE_PY.parent),
        env=base_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"migrate failed (rc={proc.returncode}):\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )


def test_qcluster_shuts_down_cleanly_on_sigint() -> None:
    """A single SIGINT to a running ``qcluster`` must exit cleanly: no #66
    ``AttributeError`` traceback, no leaked-semaphore warning, rc 0."""
    run_dir = _run_dir()
    log_path = run_dir / "qcluster.log"
    try:
        base_env = {
            **_sanitized_env(run_dir),
            "RAILWAY_VOLUME_MOUNT_PATH": str(run_dir),
            "DJANGO_SETTINGS_MODULE": "src.target_o_meter.settings",
            "VISION_DETECTOR": "mock",
        }
        _migrate(run_dir, base_env)

        log_fp = log_path.open("w")
        proc = subprocess.Popen(
            [sys.executable, str(_MANAGE_PY), "qcluster"],
            cwd=str(_MANAGE_PY.parent),
            env=base_env,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            # Wait for the cluster to actually be running before signaling.
            deadline = time.monotonic() + 20.0
            ready = False
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    pytest.fail(
                        f"qcluster exited before it started "
                        f"(rc={proc.returncode}):\n{log_path.read_text(errors='replace')}"
                    )
                if "Q Cluster" in log_path.read_text(errors="replace"):
                    ready = True
                    break
                time.sleep(0.3)
            assert ready, (
                f"qcluster did not report a cluster start within 20s:\n"
                f"{log_path.read_text(errors='replace')}"
            )

            # Mirror Ctrl+C: a single SIGINT to the qcluster main process.
            proc.send_signal(signal.SIGINT)

            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                pytest.fail(
                    "qcluster did not exit within 15s of SIGINT:\n"
                    f"{log_path.read_text(errors='replace')}"
                )
        finally:
            log_fp.close()

        log = log_path.read_text(errors="replace")

        # Clean shutdown = no Python traceback at all AND rc 0. We assert on the
        # whole failure *class*, not one exact line: the racy null-state crash
        # in ``Cluster`` (#66) trips a ``NoneType`` AttributeError, but WHICH
        # attribute (``set`` at cluster.py:84 vs ``is_set`` at :76 vs ``join``)
        # depends on exactly when the duplicate signal/atexit fires. A clean
        # run has none of them; a buggy run has a traceback. Pinning the test
        # to one line string would false-pass when the same crash lands on a
        # different attribute.
        assert "Traceback (most recent call last)" not in log, (
            f"qcluster emitted a traceback on shutdown (the #66 null-state "
            f"crash, possibly at a different attribute than 'set'):\n{log}"
        )
        assert proc.returncode == 0, (
            f"qcluster exited with rc={proc.returncode} (expected 0):\n{log}"
        )
        # Extra green-marker: leaked semaphores are an *incidental* symptom of
        # the racy teardown (timing/OS dependent — not every buggy run shows
        # it), so we only flag it when present rather than requiring it for red.
        assert "leaked semaphore objects" not in log, (
            f"leaked-semaphore warning on shutdown (racy teardown):\n{log}"
        )
    finally:
        # Best-effort cleanup; on failure the run dir is left for post-mortem.
        if proc.returncode == 0:
            import shutil
            shutil.rmtree(run_dir, ignore_errors=True)
