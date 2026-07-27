<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: S-02 photo-detection-review

- **Plan**: `context/changes/photo-detection-review/plan.md`
- **Scope**: All 8 phases (full plan review)
- **Date**: 2026-07-27
- **Verdict**: NEEDS ATTENTION
- **Findings**: 1 critical · 5 warnings · 4 observations · 1 verification-surfaced

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | FAIL (1 critical — pre-existing, surfaced here) |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS (`make check` ✓ · 51 be tests ✓ · 55 fe tests ✓) |

## Findings

### F1 — Real-looking Google API key in .env.example

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: `.env.example:5`
- **Detail**: `GOOGLE_API_KEY=key_i_need_some_computer_vision_skills_and_im_too_dumb_…` is a real-looking Google AI Studio key (matches the `key_…` shape), tracked in a public-template file. Other secrets in the file are correctly blanked. Pre-existing (added in commit 499be76 "Hole detection research part 7", Jul 21) — not introduced by S-02, but S-02 extends this file across all 8 phases so it's in scope.
- **Fix**: Replace with `GOOGLE_API_KEY=` (blank) + a comment line; rotate/revoke the key in the Google AI Studio dashboard.
  - Strength: One-line edit; matches every other secret line in the file.
  - Tradeoff: None meaningful — placeholder or not, the convention is blank.
  - Confidence: HIGH — the file's own pattern is the authority.
  - Blind spot: Haven't verified whether the key is currently live in Google's dashboard.
- **Decision**: DISMISSED — intentional Easter egg per user ("This is my Easter egg. Don't modify the example key. It's funny"). Not a real credential.

### F2 — Unbounded file.read() loads entire upload into memory

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: `src/bff/routers/scoring_routes.py:91`
- **Detail**: `storage.save_upload(file.read(), file.name)` materializes the whole upload as one `bytes` allocation. No `DATA_UPLOAD_MAX_MEMORY_SIZE` override in settings.py (verified), so a large upload drives a single allocation per concurrent request. With 3 q2 workers + SQLite broker on the same process, a few concurrent large uploads can OOM the container. Plan acknowledged this in "Performance Considerations" but deferred to S-03.
- **Fix**: Enforce an explicit upload-size cap (e.g. `DATA_UPLOAD_MAX_MEMORY_SIZE = 10MB` in settings) and/or stream via `UploadedFile.chunks()`. The cap is the simpler, smaller change.
  - Strength: Closes the DoS surface at the framework boundary; one-line settings change rejects oversized uploads before `.read()`.
  - Tradeoff: Streaming is more thorough but touches `save_upload`'s signature (passed bytes today).
  - Confidence: HIGH — Django's `DATA_UPLOAD_MAX_MEMORY_SIZE` is the documented lever.
  - Blind spot: Haven't measured real ISSF photo sizes; 10MB is a guess.
- **Decision**: FIXED — added `DATA_UPLOAD_MAX_MEMORY_SIZE` + `FILE_UPLOAD_MAX_MEMORY_SIZE` (10 MiB) to `settings.py` after the STORAGES block. Django now rejects oversized uploads with 413 before the BFF's `file.read()` runs. `manage.py check` ✓, `ruff` ✓, `test_scoring_routes.py` 11/11 ✓.

### F3 — reap_stuck_jobs() runs a write transaction on every GET poll

- **Severity**: ⚠️ WARNING
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Safety & Quality
- **Location**: `src/bff/routers/scoring_routes.py:111` → `services.py:222-238`
- **Detail**: Every `GET /v1/scoring/jobs/{id}` calls `reap_stuck_jobs()`, which opens `transaction.atomic()` + `select_for_update()` under SQLite's database-level write lock. Even when no rows match, the write lock is taken. The Waiting screen polls at 1500ms forever while the tab is open. At single-user MVP scale this is fine, but it serializes all GETs against the q2 worker's writes, and an open tab polls indefinitely.
- **Fix A ⭐ Recommended**: Move reaping to a q2 cron schedule (every 60s); let the GET just read.
  - Strength: Decouples read path from write lock; matches the service's own docstring ("intended to be called by a scheduled q2 task or the BFF-on-GET"). The 1200s timeout makes 60s cadence plenty.
  - Tradeoff: Adds a q2 Schedule row; the "always resolves" guarantee depends on the cron, not the poll. Still ≤60s staleness.
  - Confidence: HIGH — the service was designed for this.
  - Blind spot: Haven't verified the q2 Schedule API surface in this Django version.
- **Fix B**: Keep GET-side call but gate it (e.g. only when the requested job is itself non-terminal; or 1-in-N polls).
  - Strength: Smaller change; preserves the per-poll belt-and-braces.
  - Tradeoff: Still takes the write lock on the polls that DO call it.
  - Confidence: MED — partial mitigation.
  - Blind spot: The gating heuristic needs choosing.
- **Decision**: FIXED via Fix A — added vision migration `0003_schedule_stuck_job_reaper.py` (idempotent `django_q.Schedule` row, every 60s, `repeats=-1`) + a shared `_reaper_constants.py` module; removed the `reap_stuck_jobs()` call + import from `scoring_routes.get_scoring_job`. Regression tests split across the V-Model: `src/domains/vision/tests/test_reaper_schedule.py` (Schedule row exists + idempotent) and `tests/system/test_reaper_schedule.py` (BFF GET path no longer calls `reap_stuck_jobs` — kept out of the vision domain to honor the `.importlinter` "BFF Above Domains" contract). `make check` ✓ (both contracts KEPT); 25/25 reaper+scoring+q2 tests ✓.

### F4 — STUCK_RUNNING_TIMEOUT_SECONDS comment cites `retry` semantics incorrectly

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: `src/domains/vision/services.py:205`
- **Detail**: `STUCK_RUNNING_TIMEOUT_SECONDS = 1200  # 2× settings.Q_CLUSTER['retry']`. django-q2's `retry` is the retry COUNT, not seconds — the seconds-equivalent is `timeout` (=600). The 1200s threshold is reasonable (≈ 2× the 600s timeout), but the comment links it to the wrong config key. A maintainer tuning `retry` will silently shift the reap window believing the comment.
- **Fix**: Reword the comment to `# 2× settings.Q_CLUSTER['timeout'] (600s); generous headroom over the ~30s pipeline + q2 timeout`.
- **Decision**: FIXED — comment reworded to `# 2× settings.Q_CLUSTER['timeout'] (600s); generous headroom over the ~30s pipeline + q2 timeout`. `ruff` ✓.

### F5 — dev-seed.sh migrate-retry loop can fall through on persistent failure

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: `docker/dev-seed.sh:25-36`
- **Detail**: The retry loop only `break`s on success; if all 8 iterations hit "Permission denied" (SELinux relabel never completes within the window), control falls through to `echo "▸ dev seed"` and seeds against an unmigrated DB. No `eval`, no unquoted vars — script is otherwise clean.
- **Fix**: Track a `migrated=0` flag set to 1 on `break`; after the loop, `if [ "$migrated" -ne 1 ]; then exit 1; fi`.
- **Decision**: FIXED — added a `migrated=0` flag set on `break`, with a post-loop `if [ "$migrated" -ne 1 ]; then exit 1; fi` that surfaces the persistent-"Permission denied" case loudly instead of seeding an unmigrated DB. `bash -n` ✓, `test_docker_artifacts.py` 14/14 ✓.

### F6 — marked_image_url uses ScoringStorage()._storage.url, not default_storage.url `bash -n` ✓, `test_docker_artifacts.py` 14/14 ✓.

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: `src/domains/vision/services.py:317-327`
- **Detail**: Plan §4.0 said populate via `default_storage.url(...)`; impl uses `ScoringStorage()._storage.url(...)`. Justified in a multi-line comment: under FS dev, `default_storage` is rooted at MEDIA_ROOT while the marked path is relative to MEDIA_ROOT/scoring — `default_storage` would resolve against the wrong root. Deviation is more correct than the plan; only side-effect is a now-slightly-inaccurate docstring at `test_scoring_routes.py:268`.
- **Fix**: Update the test docstring to match the implementation (one-line).
- **Decision**: FIXED — updated the test docstring to name `ScoringStorage()._storage.url(...)` and explain the FS-root rationale, matching the implementation. Test still passes (1/1).

### F7 — No submission rate limit on POST /v1/scoring/jobs



- **Severity**: 💡 OBSERVATION
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Safety & Quality
- **Location**: `src/bff/routers/scoring_routes.py:77-98`
- **Detail**: Authenticated but unguarded: any logged-in user can POST at arbitrary rate, each enqueuing a q2 task. Queue is capped at 50 / 3 workers, so one user can saturate scoring for everyone. Combined with F2, this is the worst-case amplification. Acceptable for single-user MVP; flag for S-03.
- **Fix**: Defer to S-03 with a TODO, or add a per-user QUEUED+RUNNING ceiling (count rows, 429 above ~5). The ceiling is ~10 lines.
- **Decision**: DEFERRED — added a `TODO(S-03)` block on `create_scoring_job` documenting the gap (per-user QUEUED+RUNNING ceiling, ~5, 429) and the saturation risk. No code change in S-02. `ruff` ✓.

### F8 — Capture/Upload hardcode target_type='air_pistol'


- **Severity**: 💡 OBSERVATION
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Plan Adherence
- **Location**: `src/frontend/src/components/Capture.tsx:23`, `Upload.tsx:19`
- **Detail**: Both wizard screens send `target_type: 'air_pistol'` unconditionally. CaliberDistanceStep collects caliber + distance but not target type, so the SPA cannot submit a precision_pistol target even though the DTO and BFF accept it. Plan didn't explicitly mandate a target-type selector, but PRD covers both 10m air pistol AND 25m/50m precision — this is a real product gap, not a style nit.
- **Fix**: Add a target-type `<select>` to CaliberDistanceStep (air_pistol / precision_pistol), or confirm with the user that hardcoding is intentional for S-02 and add a TODO.
- **Decision**: DEFERRED — added `TODO(S-03)` blocks on both `Capture.tsx:handleFile` and `Upload.tsx:handleFile` documenting that target_type is hardcoded and that S-03 needs to add a target-type `<select>` covering all four PRD categories (`air_pistol`, `precision_pistol`, `rifle`, `shotgun`) and widen the BFF `Literal`. `tsc --noEmit` ✓.

### F9 — Dockerfile prod stage runs as root (no USER directive) `tsc --noEmit` ✓.

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: `Dockerfile` (prod stage)
- **Detail**: Prod stage inherits python:3.14-slim's default (root). Railway drops privileges at the platform layer, so this is hardening-not-critical, but Docker best practice is `USER app`. Multi-stage build, gunicorn (not runserver), and .dockerignore exclusions are all correct.
- **Fix**: Add `RUN adduser --uid 1000 --system app && USER app` to the prod stage. Optional — defer if Railway's platform-level isolation is trusted.
- **Decision**: SKIPPED — the Docker containers are local-dev/UAT only; the production deploy target is bare-metal Railway. No `USER app` hardening needed since the image never runs in prod.

### F10 — Live-server test fixture inherits `.env` via `**os.environ`, breaking isolation

- **Severity**: ⚠️ WARNING
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Safety & Quality (test isolation)
- **Location**: `tests/system/conftest.py:171` (`base_env = {**os.environ, ...}` in `_boot_runserver`)
- **Detail**: Surfaced during post-triage verification. The live-server fixture builds the spawned runserver's env as `{**os.environ, ...}`, so any var the developer's `.env` loaded via `dotenv` (notably `DEV_AUTH_BYPASS_SUB=auth0|dev-owner-sub`) leaks into the test server. With a `.env` present on disk, **6 tests fail**:
  - `test_live_server.py::test_runserver_serves_v1_me_unauthenticated`
  - `test_live_server.py::test_dev_bypass_returns_401_when_sub_unset`
  - `test_spa_auth_seam.py::test_v1_me_unauthenticated_is_401`
  - `test_spa_pipeline.py::test_index_serves_spa_shell_prod_mode`
  - `test_spa_pipeline.py::test_prod_mode_hashed_bundle_is_served_as_javascript`
  - `test_spa_pipeline.py::test_prod_mode_bundle_inlines_target_svg`

  Confirmed via `DEV_AUTH_BYPASS_SUB= uv run pytest ...` → all 6 pass. The pattern landed in `ebc4a99` (S-01 P1), so it **predates this branch** — but the user is right that unit and system tests must not depend on `.env` contents. This is a real test-isolation hole, not a cosmetic concern.
- **Fix**: In `_boot_runserver`, start from a sanitized env instead of `**os.environ`: copy `os.environ`, then explicitly drop (or override to empty) the dev-only / secret vars the live server should not inherit (`DEV_AUTH_BYPASS_SUB`, `DEV_ADMIN_*`, `GOOGLE_API_KEY`, Auth0 creds, etc.). Tests that need a specific env value already pass it via `extra_env=` (the `runserver_factory` pattern). Alternative: invert and start from a minimal allowlist (PATH, PYTHONPATH, SYSTEMROOT, etc.) — stricter but more brittle to platform differences.
- **Decision**: FIXED — added `_SANITIZED_ENV_DENYLIST` + `_sanitized_env(run_dir)` in `tests/system/conftest.py`; both `_boot_runserver` and `cli_runner.run` now build their env from `_sanitized_env(run_dir)` instead of `**os.environ`. The denylist strips dev-only + secret vars (DEV_AUTH_BYPASS_SUB, DEV_ADMIN_*, GOOGLE_API_KEY, AUTH0_*, AWS_*, OWNER_SUB_ID, USE_S3, VISION_DETECTOR, OLLAMA_*, TOM_ENV_FILE). Crucially, `_sanitized_env` also points `TOM_ENV_FILE` at an empty `.env` under the run-dir — without that, the spawned subprocess's own `load_dotenv()` would re-read the developer's `.env` and re-introduce every stripped var. Tests that need a specific value pass it via `extra_env=` (the existing pattern), which wins cleanly over the denylist. Three prod-mode boots in `test_spa_pipeline.py` now provide an explicit `SECRET_KEY` via a shared `_PROD_MODE_ENV` constant (they previously leaned on `.env`'s AUTH0_SECRET — same isolation hole, now made explicit). Full suite: 157/157 ✓, `make check` ✓, frontend 55/55 ✓.
