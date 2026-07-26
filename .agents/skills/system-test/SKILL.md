---
name: system-test
description: Run a blackbox system test against the running app — drive the CLI and/or REST API in a subprocess, assert on responses AND server logs/exit codes, and on failure reproduce the bug unit/integration-first via red-green TDD before re-running the system test fresh. Use when the user says "system test", "blackbox test", "smoke test the running app", or asks to verify the app end-to-end via a CLI command or REST endpoint.
---

# System Test (V-Model Blackbox)

Run a **system test** against the app as a black box: the implementation is done, unit + integration tests are green, and now you verify the assembled system behaves correctly when driven through its real external surface (CLI commands and/or REST endpoints served by a live process).

This is the **system-test tier of the V-Model** — not unit (one function), not integration (a few components), but the whole assembled system observed from outside. The defining property: **you treat the app as a black box.** You know its API (CLI commands, REST endpoints) and its expected outputs; you do NOT reach into its internals during the test. You watch the process from the outside — its exit code, its stdout/stderr logs, its HTTP responses.

## When to use this skill (and when NOT to)

**Prerequisites — all must hold, else STOP:**
- Implementation of the feature under test is **complete**.
- **Unit and integration tests are green.** This skill does not replace them; it runs after them. If unit/integration tests are red, fix those first — a system test is the wrong tool to debug a unit-level failure.
- The app exposes at least one observable external surface: a **CLI command** and/or a **REST API**.

**Do NOT use this skill when:**
- The bug is already reproducible at unit or integration level — debug it there first (faster, more isolated). This skill's failure loop *starts* by reproducing at unit/integration level, so if you can skip straight there, do.
- You need to test UI rendering in a browser — that's E2E/acceptance (Playwright), a different tier.
- You're verifying real third-party integrations (real OAuth, real payment) — that's UAT, gated behind real credentials and out of scope here. This skill runs against the local app with local/dev credentials only.

## The blackbox contract — what you may and may not do

| Allowed (blackbox) | Forbidden (reaches inside the box) |
|---|---|
| Launch the CLI / server as a subprocess | Import app internals *inside the test assertions* |
| Call REST endpoints over HTTP | Use the Django/Flask/etc. **test client** as the primary driver (it bypasses the real serving stack — that defeats the point) |
| Read process exit code, stdout, stderr | Reach into the process's memory / module state |
| Read the SQLite DB file the process wrote to | Mock/patch the app's internals to force a path |
| Read files the process wrote to disk |  |

**The DB is readable but not writable from the test**: you may *read* the SQLite file post-hoc to verify state changed as expected, and you may *seed* it before boot via the app's own CLI (`migrate`, `shell`, seed commands) — but you do not write to it with ORM tools directly. Seeding goes through the app's own surface so the test stays honest.

## Procedure

```
1. SETUP       → clean results/<run-id>/, fresh SQLite via env var, ensure results/ is gitignored
2. PLAN        → enumerate the surface (CLI commands / REST endpoints) + expected outputs
3. BOOT        → launch the app as a subprocess (server) or per-invocation (CLI)
4. DRIVE       → happy path first, then business-error cases, then assert on runtime errors
5. VERIFY      → check responses + logs + exit codes + (if needed) the DB file
6. CLEANUP     → tear down processes; results/ stays for post-mortem until the next run
7. ON FAILURE  → reproduce unit/integration-first (red-green), fix, then RE-RUN SYSTEM TEST FRESH
```

### Step 1 — Setup: clean context under `results/`

**Every run starts in a clean `./results` directory.** This is non-negotiable — it guarantees no state from a previous run leaks in (the dev-bypass nick-collision bug reproduced precisely because two runs shared state in `src/db.sqlite3`).

1. Ensure `results/` is gitignored. Read `.gitignore`; if `results/` (or `results`) is absent, **append it**:
   ```
   results/
   ```
   Commit that `.gitignore` line as part of the test scaffolding — never commit `results/` contents.

2. Create a per-run subdirectory: `results/<run-id>/` where `<run-id>` is `<test-name>-<uuid8>` (e.g. `results/test_dev_bypass_colliding-a1b2c3d4/`). The harness allocates this for you and names it uniquely per boot — never hand-pick a fixed name, or parallel runs collide. **Everything ephemeral for that run lives there**: the SQLite DB, the captured `runserver.stderr`, CLI stdout/stderr, output files, seed-script outputs.

3. **Keep failed-run artifacts; clean successful ones.** A passing test `rm -rf`s its run dir (keeps `results/` tidy); a failing test leaves it in place so you can read `runserver.stderr` and the DB file during the failure loop (Step "On failure"). The harness implements this via a `pytest_runtest_makereport` hook that threads the test outcome onto the fixture — do not delete the run dir yourself in the test body.

4. **Fresh SQLite per run, pointed at by an env var.** The app already reads its DB path from an environment variable (`RAILWAY_VOLUME_MOUNT_PATH` in this repo — settings.py derives `db.sqlite3` under it). Point that env var at `results/<run-id>/` so the subprocess and any seed/migrate commands all agree on the same throwaway DB. **Never reuse the developer's `src/db.sqlite3`** — a stale row there can both mask and cause bugs.

### Step 2 — Plan: enumerate the surface + expected outputs

Before driving, write down (in the test's docstring or a scratch note):
- The **external surface**: exact CLI commands (`manage.py <cmd> --flag`) and/or REST endpoints (`METHOD /path`) you will invoke.
- The **expected outputs**: HTTP status + body shape, or CLI exit code + stdout substring, or DB row state after the call.
- The **cases**: (a) happy path, (b) business errors (invalid payload, duplicate, unauthorized), (c) runtime errors you want to *rule out* (no 500, no traceback in logs, no non-zero exit).

A test without explicit expected outputs is not a blackbox test — it's just "I ran it and nothing exploded." State the contract.

### Step 3 — Boot: launch the app as a subprocess

**REST API** — launch the server once, drive it with many calls:
- Boot the server as a subprocess in the background (see `references/live-server-harness.md` for the full `runserver`/`runserver_factory` fixture this repo uses — copy its shape, do not reinvent).
- Boot with `--noreload` so there is exactly one PID to terminate (the autoreloader forks and complicates teardown).
- Boot on an **ephemeral port** (bind a socket to port 0, read the assigned port) — never hardcode `:8000`; parallel test runs would collide.
- **Capture stderr to a file** under `results/<run-id>/` (e.g. `runserver.stderr`). This is your primary runtime-error oracle — tracebacks land here.
- **Poll for readiness** before driving: hit a known endpoint (e.g. `GET /v1/me`) in a loop until it returns *any* HTTP status, the process dies, or you time out (~30s). Do not assume the server is up the instant the subprocess launches.

**CLI** — one subprocess per invocation (see `references/cli-harness.md` for the full `cli` fixture + a worked example against the vision CLI):
- Use the `cli` factory fixture: it runs each command with `cwd` set to a fresh `results/<run-id>/` so output files land isolated, captures stdout + stderr + exit code, and (like `runserver`) keeps the run dir on failure.
- Run the command as the operator would type it (`uv run python -m src.domains.vision …`, `node dist/cli.js …`) — don't import the CLI's functions and call them in-process; that defeats the blackbox contract.
- Assert on **exit code + stderr + (stdout OR output file)** together. `rc == 0` alone is weak — a `try/except: pass` swallows the exception and still exits 0; the traceback lands in stderr. Pair `result.assert_success()` (which checks `rc == 0` AND no traceback) with a stdout/output-file assertion.
- For business-error cases (invalid args, bad input), assert the command exits non-zero with a clean usage message — NOT a traceback. `argparse` usage errors exit `2`; a command's own `sys.exit(1)` is a handled failure. Either way, `assert_no_traceback()` distinguishes "handled error" from "crash".

In both cases, pass the **env var from Step 1** (DB path) plus any feature env (`DEBUG=True`, `DEV_AUTH_BYPASS_SUB`, etc.) to the subprocess explicitly — do not rely on the parent shell's env.

### Step 4 — Drive: happy path → business errors → runtime errors

Order matters: **happy path first.** If the happy path 500s, stop and debug — there's no point asserting error cases against a broken baseline.

Then cover:
- **Business errors** — feed an invalid payload / duplicate / unauthorized request and assert the app rejects it *correctly* (the right 4xx status, the right error message). A 500 here is a runtime-error bug masquerading as a business-error test.
- **Runtime errors** — these are the cases where you assert the *absence* of failure: "no traceback in stderr," "exit code 0," "no `IntegrityError`/`UNIQUE constraint` in the log." See `references/failure-playbook.md` for the log-grep vocabulary.

### Step 5 — Verify: responses, logs, exit codes, DB

For each case, assert on **at least two** independent signals:
- **Response/exit code** — the externally-visible outcome.
- **Logs** — `assert_no_traceback()` against the captured stderr (and/or grep for specific error strings you expect or want to rule out).
- **DB file** (optional, for state-changing calls) — read the SQLite file from `results/<run-id>/` to confirm rows changed as expected. Read-only; never write.

Two signals catch what one misses: a request can return 200 yet still have logged a swallowed exception; a CLI can exit 0 yet have written a corrupt row.

### Step 6 — Cleanup

- Terminate the server subprocess (and its process group — boot with `start_new_session=True` so `os.killpg` reaches the whole tree).
- Leave `results/<run-id>/` on disk until the next run of the same test — it's the post-mortem artifact. The *next* run recreates it clean (Step 1).

## On failure — the unit/integration-first loop

This is the load-bearing rule. When a system test fails (a 500, a traceback, a wrong response, a non-zero exit):

**Do NOT start by patching the running app.** Start by reproducing the failure at the **cheapest tier that can express it**, almost always unit or integration:

```
1. READ        → read the captured logs (results/<run-id>/runserver.stderr) + the DB file;
                 form a one-sentence hypothesis: "X happens because Y."
2. REPRODUCE   → write a FAILING unit or integration test that reproduces the hypothesis
                 (red). The Django test client is fine here — it's fast and isolated.
3. FIX         → minimal production change to turn the repro test green.
4. RE-RUN      → re-run the unit/integration suite; confirm green.
5. SYSTEM TEST → re-run the SYSTEM TEST in a FRESH results/<run-id>/ (clean DB, new process).
                 Only the system test proves the assembled system is fixed — the unit
                 repro proves the cause, the system test proves the fix holds end-to-end.
6. LAND BOTH   → the repro test becomes a permanent regression guard; the system test
                 pins the blackbox contract.
```

Why unit/integration first: a system test takes seconds-to-tens-of-seconds to run, boots a real process, and isolates causes poorly (everything runs in one server). A unit repro runs in milliseconds and points straight at the faulty code. The dev-bypass nick-collision bug was debugged this way: the system test showed the 500, a unit repro (`User.objects.get_or_create` with two colliding subs) reproduced the `IntegrityError` in 50ms, the fix went in, then the system test re-confirmed it green in a fresh DB.

Read `references/failure-playbook.md` for the log-vocabulary, the hypothesis checklist, and the full worked example.

## File placement (V-Model convention)

- **System tests** → `tests/system/` (this repo's convention; they co-locate with the existing Django-test-client system tests). Run via `make system-test`.
- **Shared blackbox harness** (the `runserver`/`runserver_factory` fixtures) → `tests/system/conftest.py`. One harness, reused by every blackbox test — do not copy a fresh subprocess wrapper into each test file.
- **UAT (real third-party creds)** stays in `tests/acceptance/` — do not blur the line. A blackbox system test runs locally with local/dev credentials; UAT runs against real Auth0/etc.

## Gotchas (read before you start — these defy reasonable assumptions)

- **A shared DB across runs both masks and manufactures bugs.** The dev-bypass collision reproduced only because two runs shared `src/db.sqlite3`. Always point the DB env var at a fresh `results/<run-id>/`. This is the #1 cause of "works on my machine, fails in CI."
- **The Django test client is NOT a system test.** It never exercises the real serving stack (WSGI, middleware ordering on a live `request.user`, the actual URL routing through the server). Bugs like the nick collision only surface on the real subprocess path. Use the test client for unit/integration; use a real subprocess for system tests.
- **`runserver` autoreload forks.** Without `--noreload` you get two processes and a dangling child on teardown. Always `--noreload`.
- **Hardcoded ports collide.** Parallel runs (CI matrices, two terminals) hit `EADDRINUSE`. Always bind to port 0 and read the assigned port back.
- **pytest-django ships its own `live_server` fixture.** Do not name your subprocess fixture `live_server` — it collides and raises `AttributeError: '_live_server_modified_settings'`. This repo's fixture is named `runserver` / `runserver_factory` for exactly this reason.
- **Env flipped via `monkeypatch.setenv` AFTER boot does NOT reach the running server.** The server's module-level cache (e.g. `_dev_user`) is already populated. To test a different boot-time env, boot a fresh server (`runserver_factory`), don't mutate the live one.
- **A 200 response can still hide a swallowed exception.** Always pair the status assertion with `assert_no_traceback()` on the captured stderr. The response says "ok"; the log says "I caught an error and returned 200 anyway."
- **`get_or_create` lies about idempotency when the `defaults` collide.** Two distinct keys whose `defaults` (e.g. derived nick) match an existing row will INSERT and raise `IntegrityError` — `get_or_create` does not catch constraint violations on the `defaults`, only on the lookup key. Any code deriving a unique-field default from a low-entropy input has this hazard.

## References (load on demand)

- **`references/live-server-harness.md`** — Read when the test drives a **long-running REST server**. Full source of the `runserver` / `runserver_factory` fixtures (subprocess boot, ephemeral port, readiness poll, stderr capture, keep-on-failure teardown), with repo-specific values marked `# repo-specific — adapt`.
- **`references/cli-harness.md`** — Read when the test drives a **one-shot CLI command**. Full source of the `cli` fixture (per-invocation subprocess, exit-code + stdout + stderr capture, output-file assertions) + a worked example against the vision CLI (`python -m src.domains.vision … --detector mock`).
- **`references/failure-playbook.md`** — Read when a system test FAILS. Contains the log-grep vocabulary (`Traceback (most recent call last)`, `IntegrityError`, `UNIQUE constraint`, etc.), the hypothesis checklist, and the worked dev-bypass nick-collision example end-to-end (system-test red → unit repro → fix → system-test green).
