# CLI harness (reference)

Load this when the system test drives a **CLI command** (not a long-running server). CLI blackbox tests are simpler than server tests — one subprocess per invocation, no port, no readiness poll — but they have their own contract: assert on the **exit code**, **stdout**, and **stderr** together, and keep the working directory + outputs isolated under `results/<run-id>/`.

Use this whenever the app exposes a command the operator runs once-and-done (a data pipeline, a migration helper, a one-shot job). If the surface is a long-running server answering many requests, use `references/live-server-harness.md` instead.

## The CLI contract — what to assert

Every CLI invocation produces three observable signals. Assert on **at least two** (the same two-signal rule as the REST path — one signal hides what another catches):

| Signal | Success looks like | Failure looks like |
|---|---|---|
| **Exit code** | `0` | non-zero (the command's contract defines which: `1` general error, `2` usage error, etc.) |
| **stdout** | the expected payload / summary (assert a substring or parse the JSON) | empty, or a partial/garbled payload |
| **stderr** | empty (or only warnings you accept) | a Python traceback, a stack trace, an error message |
| **Output files** (optional) | the expected file written with expected shape | missing, or present but corrupt/empty |

The most common CLI bug the logs catch but the exit code misses: a command that returns `0` after swallowing an exception (`try/except: pass`), or one that writes a corrupt output file. Always pair `rc == 0` with a stderr-traceback check (`assert b"Traceback" not in result.stderr`) and, for file-producing commands, an output-file assertion.

## The fixture shape (copy this — do not reinvent)

```python
"""Shared CLI-runner fixture for blackbox CLI system tests."""
from __future__ import annotations

import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_RESULTS_ROOT = _REPO_ROOT / "results"


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


def _run_dir_for(test_id: str) -> Path:
    run_id = f"{test_id}-{uuid.uuid4().hex[:8]}"
    run_dir = _RESULTS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


@pytest.fixture
def cli(request: pytest.FixtureRequest):
    """Factory: run a CLI command in a clean run dir under ``results/``.

    Yields ``run(argv, *, extra_env=None, cwd=None) -> CliResult``. Each call
    gets its own ``results/<test>-<uuid>/`` as cwd (so output files land there,
    isolated from the dev tree). Like the server fixture, failed runs keep
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
        import os
        env = {**os.environ, **(extra_env or {})}
        proc = subprocess.run(
            argv,
            cwd=str(cwd or run_dir),
            env=env,
            capture_output=True,
            timeout=120,
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


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    """Thread the test outcome onto each CliResult so teardown can decide
    keep-on-failure vs clean-on-success."""
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        for attr in ("_cli_results", "_live_server", "_live_servers"):
            val = getattr(item, attr, None)
            if val is None:
                continue
            results = val if isinstance(val, list) else [val]
            for r in results:
                r.test_failed = True
```

## Using the fixture — worked example: the vision CLI

The vision domain ships a standalone CLI (`src/domains/vision/__main__.py`):

```
uv run python -m src.domains.vision <IMAGE_PATH>... --detector {google,ollama,mock}
```

It writes a `_summary.json` per image. A blackbox system test drives the `mock` detector (no API key needed) against a fixture image and asserts on exit code + stderr + the output file:

```python
"""System test: the vision CLI, driven as a blackbox (mock detector)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_IMAGE = _REPO_ROOT / "resources" / "train" / "images" / "sample-target.jpg"

pytestmark = [pytest.mark.django_db, pytest.mark.dev]


def test_vision_cli_mock_detector_writes_summary(cli) -> None:
    """Happy path: the mock detector scores the fixture image and writes a
    well-formed ``_summary.json`` — exit 0, no traceback, JSON parses."""
    if not _FIXTURE_IMAGE.exists():
        pytest.skip(f"fixture image absent: {_FIXTURE_IMAGE}")

    result = cli(
        [
            "uv", "run", "python", "-m", "src.domains.vision",
            str(_FIXTURE_IMAGE),
            "--detector", "mock",
            "--no-gt",
        ],
        # The mock detector needs no creds; pin it off to keep the test honest.
        extra_env={"GOOGLE_API_KEY": "", "OLLAMA_HOST": ""},
    )

    # Signal 1 + 2: exit code + stderr.
    result.assert_success()

    # Signal 3: the output file exists and is valid JSON with the expected shape.
    summaries = list(result.run_dir.rglob("*_summary.json"))
    assert summaries, f"no _summary.json written under {result.run_dir}"
    payload = json.loads(summaries[0].read_text())
    assert "score" in payload, f"summary missing 'score': {payload}"


def test_vision_cli_rejects_unknown_detector(cli) -> None:
    """Business-error case: an invalid ``--detector`` value must exit non-zero
    with a usage message — NOT crash with a traceback."""
    result = cli(
        [
            "uv", "run", "python", "-m", "src.domains.vision",
            str(_REPO_ROOT / "resources" / "train" / "images" / "sample-target.jpg"),
            "--detector", "nonexistent",
        ],
    )
    # argparse rejects unknown choices with rc == 2 and a usage message on stderr.
    assert result.returncode != 0
    result.assert_no_traceback()  # a traceback here = unhandled crash, not a clean arg error
```

## Adapting to another CLI

The shape generalizes; swap the specifics:
- **Command prefix**: `uv run python -m <pkg>` / `node dist/cli.js` / `./bin/myapp` — whatever the operator types.
- **Timeout**: bump for long pipelines (CV, ML) — 120s is a starting point, not a ceiling.
- **Success signal**: not every CLI writes a file — some print JSON to stdout, some mutate a DB (read the `results/`-pointed DB file to verify). Pick the observable that proves the command did its job.
- **Argparse-vs-handled-error exit codes**: `argparse` uses `2` for usage errors; a command's own `sys.exit(1)` is a handled failure. A Python traceback usually exits `1` but the *traceback in stderr* is the real signal — don't rely on the exit code alone to distinguish a handled error from a crash.

## When the CLI and the server both need testing

Some features surface on both (e.g. a CLI seed command and a REST endpoint that reads the seeded data). Test each through its own blackbox surface — don't seed via the REST API to test the CLI, or vice versa. The two fixtures (`cli`, `runserver`) are independent; a single test can use both if it must, but each assertion should target the surface that produced the signal.
