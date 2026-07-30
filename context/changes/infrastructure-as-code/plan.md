# GitHub Actions CI/CD + Railpack Railway IaC Implementation Plan

## Overview

Build a modular GitHub Actions CI/CD pipeline (PR gate on `pull_request`, auto-deploy on `push: master`) from 8 composite actions, plus a Railpack-driven Railway IaC declaring the single-service `web` topology for the Free tier. The plan pivots from the originally-researched Dockerfile deploy path to **Railpack-native deploy with uv** per user direction, overriding `infrastructure.md:93`'s Risk Register mandate. Production secrets are provisioned manually by the human in the Railway/GitHub dashboards — at no point does the agent touch a production token.

## Current State Analysis

- **Existing CI**: `.github/workflows/uat.yml` is the only workflow file — pins `astral-sh/setup-uv@v3` (stale) and `actions/setup-python@v5` (3.14), gates on `vars.UAT_ENABLED == 'true'` and `github.repository == 'krkruk/target-o-meter'`. The Python UAT suite it would run has **no test file yet** (`tests/acceptance/conftest.py` only). Per user direction this file is **deleted** in Phase 1.
- **Existing build path**: Root `Dockerfile` (125 lines) has `dev` and `prod` stages; both install opencv apt deps, sync uv deps, and build the frontend bundle. `docker-compose.dev.yml` (targets `dev`) and `docker-compose.prod.yml` (targets `prod`) use it for **local dev only**. Per user direction the deploy path becomes **Railpack**, with the Dockerfile retained only for local dev and `BUILDER=railpack` overriding Railway's auto-detection.
- **Existing IaC**: none. `.railway/` does not exist. The research draft in `research.md` is the starting point but assumed Dockerfile-path; this plan adapts it to Railpack.
- **Test suite isolation** (`research.md:67-72`): BE unit/integration, FE vitest, and `tests/system/` are env-hermetic (zero GitHub secrets; system tests force `VISION_DETECTOR=mock`). Only acceptance needs additional browser install. CI can run four of the five suites with no secret setup.
- **OpenCV apt requirement**: `opencv-python-headless` requires `libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1` at runtime. Railpack's auto-detected system-deps list (`research.md:436`, confirmed in railpack.com/languages/python) does NOT include opencv — must be declared explicitly.
- **`pyproject.toml` name is `module1`** (placeholder), not `target_o_meter`. This makes `RAILPACK_DJANGO_APP_NAME` load-bearing under Railpack — the `lessons.md:5-10` rule the research dismissed as "Dockerfile-only, N/A" is now ACTIVE.

## Desired End State

1. Every PR triggers CI: lint → BE unit/integration + FE unit (parallel) → system tests. Red blocks merge.
2. Every merge to `master` triggers CD: the same chain plus JS Playwright E2E acceptance, then auto-deploys to Railway via `railway up` against the production environment.
3. `.railway/railway.ts` declares the full single-service Free-tier topology; the human provisions project + secrets manually first, then runs `railway config apply` once locally; thereafter CI deploys via `railway up` (no IaC reconciliation in CI).
4. `railpack.json` at repo root fully describes the bare-metal build: Python 3.14, opencv apt deps, frontend build, collectstatic, WSGI app name.
5. `/health` view returns 200 so Railway's healthcheck is a real readiness signal.
6. The 4 "verify-on-first-apply/deploy" unknowns (bucket creds, Tigris endpoint, image size, cold-start latency) plus the Free-RAM headroom check have explicit verification steps and a documented Hobby ($5) escape hatch.

### Key Discoveries

- **Railpack is the bare-metal path** (railpack.com): uv-driven Python build, BuildKit-backed, natively integrated into Railway. Python+uv supported via `pyproject.toml` + `uv.lock`.
- **Railpack Django default start command** is `python manage.py migrate && gunicorn {appName}:application` (railpack.com/languages/python) — close to what we need but missing qcluster + `--workers 1` + the full WSGI path. Override required.
- **Railpack's `start` runs post-volume-mount** (same as Dockerfile path), so migrate-in-start remains correct; `preDeployCommand` would NOT have the volume mounted (`research.md:184`).
- **`RAILWAY_VOLUME_MOUNT_PATH` is auto-injected** by Railway from `volumeMounts` — must NOT be set manually (`settings.py:266`, `research.md:183`).
- **`environment: production` on the CD deploy job** is the GitHub Actions mechanism that gates `RAILWAY_TOKEN` access — even a rogue workflow on master can't exfiltrate it without that env gate (`research.md:174`).
- **Concurrency discipline**: cancel PR runs aggressively (`cancel-in-progress: true`); **never** cancel master deploys (`cancel-in-progress: false`) — `railway up` returns on build-trigger, not build-complete; cancelling races Railway's builder (`research.md:117`).
- **The 2-service web+worker topology is impossible** for SQLite + django-q2 ORM broker (1 Volume → 1 service; SQLite has no network listener) — `research.md:41-54`. Single-service is the working shape.
- **Free-tier budget math** (`research.md:508-521`): realistic resident set ~250-400MB against the 512MB cap — tight but viable ONLY with sleep mode ON. Q2_WORKERS=1, gunicorn `--workers 1` are mandatory to fit.

## What We're NOT Doing

- **No Postgres migration.** The 2-service topology that would require Postgres is documented as a future evolution, not in scope (`research.md:316-337`).
- **No Trial-tier bring-up.** User has already consumed the Trial credit — verification horizon ends at Free-tier bring-up, with Hobby ($5) as the documented escape hatch.
- **No required-reviewer deploy gate.** Auto-deploy after tests pass; rollback is the recovery path for test-passing-but-broken-in-prod.
- **No external health-check ping.** Keeping the service warm defeats sleep-mode RAM savings (`research.md:578`).
- **No Python UAT re-introduction.** Deleting `uat.yml` removes its runner; when a real UAT test file lands, it should be re-added as a CD job consuming the new composites. Out of scope here.
- **No `railway config apply` in CI.** IaC reconciliation is a manual one-shot by the human; CI only runs `railway up`.
- **No production secret values in plan, IaC, or agent context.** All secrets use `preserve()` placeholders; values are set in dashboards by the human only.
- **No Dockerfile deletion.** Kept for local `docker compose` dev only.
- **No `supervisord`/`s6-overlay`.** MVP ships the `sh -c "migrate && qcluster & exec gunicorn"` form; supervisord is a fast-follow (`research.md:306`).
- **No Free→Hobby tier transition verification.** That's an operational decision when/if the Free RAM budget blows; documented as escape hatch only.

## Implementation Approach

Eight sequential phases. Phases 1-3 are independent foundation work (Django changes, Railpack config, composite actions); Phases 4-5 consume the composites; Phase 6 writes the IaC; Phases 7-8 are operational (manual provisioning + first-deploy verification). Each phase has automated verification where possible and explicit manual verification where automated is impossible (e.g., dashboard clicks, real platform behavior).

The plan's hardest verification items — the four "verify on first apply/deploy" unknowns — land in Phase 8 as a structured checklist with a documented action for each failure mode, including the Hobby upgrade path if Free-tier RAM headroom proves insufficient.

## Critical Implementation Details

- **`infrastructure.md:93` is overridden by user direction.** The Risk Register's "use Dockerfile deploy path instead of Railpack auto-detection" mandate is reversed: this plan ships Railpack. The mitigation (opencv apt deps via `RAILPACK_DEPLOY_APT_PACKAGES`, formerly provided by the Dockerfile) preserves the original concern. Phase 8 verifies the build succeeds and opencv imports cleanly.
- **`RAILPACK_DJANGO_APP_NAME=target_o_meter.wsgi` is load-bearing** under Railpack. Without it, Railpack's Django detector falls back to scanning Python files for `WSGI_APPLICATION` — fragile given our `src/` layout — and may construct `gunicorn module1:application` (the `pyproject.toml` name), which fails to find the attribute and crash-loops. This is the `lessons.md:5-10` rule the research dismissed; it is now ACTIVE.
- **`Q2_WORKERS` env var is inert until Phase 1 lands.** The IaC declares `Q2_WORKERS: "1"`; until `settings.py` reads it (`Q_CLUSTER['workers'] = int(os.environ.get("Q2_WORKERS", "3"))`), the value is unused and the local default of 3 stays in effect.
- **Migrate runs in `start`, not `preDeployCommand`.** Railway volumes are not mounted during pre-deploy; migrate must run post-mount against `/data/db.sqlite3` (`research.md:184`).
- **Secret posture is non-negotiable.** No `railway_set_variables` MCP calls for any secret, no secret values in `.railway/railway.ts` (use `preserve()`), no secret values in plan or commits. The human provisions every secret in dashboards; the agent only declares the placeholders.
- **Railway Volume and Bucket must exist before Phase 8's first `railway config apply`.** Chicken-and-egg: the IaC references them by name, but Railway creates the underlying resources imperatively. Create them via dashboard or MCP first, then `config apply` reconciles the IaC against the existing resources without clobbering injected bucket creds (which `preserve()` guards).
- **`BUILDER=railpack` override required.** Railway auto-uses a root `Dockerfile` if present. Since the Dockerfile is retained for local dev, the Railway service must explicitly set `BUILDER=railpack` to force Railpack.

## Phase 1: Django Foundation Changes

### Overview

Three small, related changes that unblock the IaC and the deploy: a `/health` view, an env-overridable q2 worker count, and deletion of the dead `uat.yml` workflow.

### Changes Required:

#### 1. `/health` endpoint

**File**: `src/target_o_meter/urls.py` (route registration); `src/target_o_meter/views.py` (or a new `src/target_o_meter/health_views.py` — follow existing convention in the file)

**Intent**: Give Railway a real readiness signal so the healthcheck in the IaC can distinguish "gunicorn booted" from "ready to serve." Without it, Railway uses process liveness only and a slow boot looks healthy before migrate finishes.

**Contract**: A view that returns `HttpResponse("ok")` with status 200, no DB access, no auth. Wired at path `/health` in `urlpatterns`. Must not be gated by `DEBUG` or any auth middleware (Railway's prober has no session).

#### 2. Make `Q_CLUSTER['workers']` env-overridable

**File**: `src/target_o_meter/settings.py` (around line 276-286, the `Q_CLUSTER` block)

**Intent**: Allow Railway to narrow q2 to 1 worker to fit Free-tier RAM. Local default stays 3 so `make dev` is unaffected.

**Contract**: Change `'workers': 3` to `'workers': int(os.environ.get('Q2_WORKERS', '3'))`. The IaC sets `Q2_WORKERS=1`. Inert until this change lands.

#### 3. Delete `.github/workflows/uat.yml`

**File**: `.github/workflows/uat.yml`

**Intent**: Remove the stale `setup-uv@v3`-pinning, never-enabled, no-test-file workflow. The Python UAT surface loses its runner; when a real UAT test file lands, it should be re-added as a CD job consuming the new composites (out of scope here).

**Contract**: File removed. Document this tradeoff in `## Migration Notes` below.

### Success Criteria:

#### Automated Verification:

- `uv run pytest` passes (BE unit + integration unaffected by `/health` view + settings change).
- `uv run ruff check .` passes (lint clean on new view + settings edit).
- `uv run python src/manage.py check` passes (Django system checks, including `E001`/`E002`/`W001`/`W002` guards).
- `uv run python src/manage.py migrate --no-input` applies cleanly against a fresh test DB (proves `/health` route loads).
- Local curl smoke check: `uv run python src/manage.py runserver &` then `curl -fsS http://127.0.0.1:8000/health` returns `ok` with status 200.
- `Q2_WORKERS=1 uv run python src/manage.py check` passes (proves the env var is read without error).

#### Manual Verification:

- `Q2_WORKERS=1 uv run python src/manage.py qcluster` boots with 1 worker (visible in q2 startup log: "1 Workers").
- `make dev` still boots with 3 workers (default unchanged locally).
- Confirm `.github/workflows/uat.yml` is gone from the repo root.

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 2: Railpack Configuration

### Overview

Write `railpack.json` at the repo root declaring the bare-metal build: Python 3.14, opencv apt deps, frontend npm build, collectstatic, and the Django app name. This is the single source of truth for the prod build; Railway reads it automatically when `BUILDER=railpack` is set on the service.

### Changes Required:

#### 1. `railpack.json`

**File**: `railpack.json` (new, at repo root)

**Intent**: Tell Railpack exactly how to build the app — pin the Python version, declare the opencv runtime deps that Railpack can't auto-detect, sequence the frontend build before collectstatic, and override the start command to run qcluster + gunicorn with the right WSGI path.

**Contract**: JSON file conforming to `https://schema.railpack.com`. Must express:
- Python version pin: `RAILPACK_PYTHON_VERSION=3.14` (or the railpack.json equivalent). Document the `MISE_PYTHON_COMPILE=1` fallback inline as a comment or note in case Mise lacks a precompiled 3.14 binary at build time.
- Deploy apt packages: `libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1` (the opencv-python-headless runtime deps from `Dockerfile:31-43`). Use the railpack.json `deploy.aptPackages` field with `"..."` prefix to extend Railpack's auto-generated list rather than replace it.
- `RAILPACK_DJANGO_APP_NAME=target_o_meter.wsgi` per `lessons.md:5-10` — non-negotiable given `pyproject.toml` name is `module1`.
- A custom build step that: (a) runs `npm ci && npm run build` inside `src/frontend/`, (b) runs `python src/manage.py collectstatic --noinput --clear`. The Node toolchain is provided by Railpack's Node provider (`RAILPACK_PACKAGES=node` per railpack.com/guides/installing-packages).
- A start command override: `sh -c "python src/manage.py migrate --noinput && python src/manage.py qcluster & exec gunicorn src.target_o_meter.wsgi:application --bind 0.0.0.0:8000 --workers 1"`. Note: under Railpack the venv is activated so `python`/`gunicorn` resolve without `uv run` prefix; verify on first build.

#### 2. `.gitignore` update (if needed)

**File**: `.gitignore`

**Intent**: Ensure railpack-local artifacts (if any) are ignored. Likely a no-op since railpack.json is the only file and it's committed, but verify nothing else lands at repo root during local railpack experimentation.

**Contract**: No new ignore rules required unless local railpack testing creates build outputs.

### Success Criteria:

#### Automated Verification:

- `railpack.json` parses as valid JSON: `python -c "import json; json.load(open('railpack.json'))"`.
- Schema validation against `https://schema.railpack.com` (if a validator is available; otherwise visual review against railpack.com/config/file).
- `uv run python src/manage.py check` passes (no settings change here, but proves Django still boots against the existing config).
- If Docker + BuildKit are available locally: `railpack build .` succeeds end-to-end (this is the strongest signal that Phase 8's Railway build will succeed). **If Docker/BuildKit are unavailable, skip this and rely on Phase 8's first Railway build as the verification** — document this in the Verification checklist.

#### Manual Verification:

- Visual review of `railpack.json` against railpack.com/languages/python + railpack.com/guides/installing-packages: every required field present, apt package list matches `Dockerfile:31-43`, start command matches the target shape.
- Confirm `RAILPACK_DJANGO_APP_NAME=target_o_meter.wsgi` is present and correctly spelled (a typo here crash-loops gunicorn in prod).
- Confirm `BUILDER=railpack` is NOT in railpack.json (it's a Railway service env, not a Railpack config) — it lands in the IaC in Phase 6.

**Implementation Note**: Pause for manual confirmation before proceeding.

---

## Phase 3: CI Composite Actions

### Overview

Write 8 reusable composite actions under `.github/actions/<name>/action.yml`. Each action is a single concern; the CI/CD workflows in Phases 4-5 compose them. No `Dockerfile` changes here — the composites run on `ubuntu-latest` GitHub runners, not Railpack.

### Changes Required:

#### 1. `setup-backend` composite

**File**: `.github/actions/setup-backend/action.yml`

**Intent**: Install opencv apt deps, set up Python 3.14, install uv (pinned @v6 — bump from the stale v3 in `uat.yml`), and sync all dep groups. Reused as the first step of every backend-bearing job.

**Contract**: Composite action with `steps:` that: (a) `sudo apt-get update && sudo apt-get install -y libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1`, (b) `uses: actions/setup-python@v5` with `python-version: "3.14"`, (c) `uses: astral-sh/setup-uv@v6`, (d) `run: uv sync --frozen --all-groups`. Cache the uv cache dir via `actions/cache@v4` keyed on `uv.lock`.

#### 2. `setup-frontend` composite

**File**: `.github/actions/setup-frontend/action.yml`

**Intent**: Install Node 20 LTS + `npm ci` in `src/frontend/`. Reused by FE test jobs and the acceptance job.

**Contract**: Composite action with `steps:` that: (a) `uses: actions/setup-node@v4` with `node-version: 20` and `cache: npm` scoped to `src/frontend`, (b) `run: npm ci` with `working-directory: src/frontend`.

#### 3. `lint-check` composite

**File**: `.github/actions/lint-check/action.yml`

**Intent**: Run the repo's existing verification gate — ruff + import-linter + FE tsc — so CI fails fast on lint errors before any test suite burns minutes.

**Contract**: Composite action with `steps:` that: (a) `run: uv run ruff check .` (no `--fix` — CI doesn't mutate files; the `[2/5]` re-check from `make check` IS the gate), (b) `run: uv run lint-imports`, (c) `run: npm run lint` with `working-directory: src/frontend` (= `tsc --noEmit`).

#### 4. `run-be-tests` composite

**File**: `.github/actions/run-be-tests/action.yml`

**Intent**: Run the BE unit + integration suite (env-hermetic, zero secrets per `research.md:67`).

**Contract**: Composite action: `run: uv run pytest`. No secrets, no env injection.

#### 5. `run-fe-tests` composite

**File**: `.github/actions/run-fe-tests/action.yml`

**Intent**: Run the FE vitest suite (jsdom, API mocked per `research.md:68`).

**Contract**: Composite action: `run: npm run test` (= `vitest run`) with `working-directory: src/frontend`.

#### 6. `run-system-tests` composite

**File**: `.github/actions/run-system-tests/action.yml`

**Intent**: Run the system-test suite — boots Django via `manage.py runserver` subprocess, exercises the BFF via httpx, no browser (`research.md:69`).

**Contract**: Composite action: `run: uv run pytest tests/system`. The conftest sanitizes env and forces `VISION_DETECTOR=mock`; no secrets needed.

#### 7. `run-acceptance-tests` composite

**File**: `.github/actions/run-acceptance-tests/action.yml`

**Intent**: Run the JS Playwright E2E suite in dev mode (`VISION_DETECTOR=mock`, dev-bypass auth, zero secrets/cost per `research.md:599-618`). The Playwright `globalSetup` self-boots Django + Vite + qcluster — no manual stack boot in the workflow.

**Contract**: Composite action with `steps:` that: (a) `run: uv run playwright install --with-deps chromium` (Python group ships playwright; provides browser binary + system deps), (b) `run: npx playwright install --with-deps chromium` with `working-directory: src/frontend` (JS runner's browser binary), (c) `run: npm run test:acceptance` (= `playwright test`) with `working-directory: src/frontend`.

#### 8. `deploy-railway` composite

**File**: `.github/actions/deploy-railway/action.yml`

**Intent**: Deploy to Railway via CLI. **Only runs `railway up` — no `config apply`** (IaC is reconciled once manually by the human in Phase 8; CI never reconciles IaC).

**Contract**: Composite action with `steps:` that: (a) install Railway CLI: `curl -fsSL cli.new | sh`, (b) `run: railway up` with env `RAILWAY_TOKEN`, `RAILWAY_PROJECT_ID`, `RAILWAY_ENVIRONMENT_ID` sourced from the workflow job's env (which sources them from GitHub `secrets`/`vars`). The composite declares these as inputs or env vars; the workflow job passes them in. **No `config apply` step** — document the rationale in the file comment.

### Success Criteria:

#### Automated Verification:

- All 8 `action.yml` files parse as valid YAML: `python -c "import yaml; yaml.safe_load(open('.github/actions/<name>/action.yml'))"` for each.
- `actionlint` (if installed; super-linter provides it) passes on every composite.
- Visual diff: every composite's `name:`, `description:`, and `inputs:` (where applicable) are present.

#### Manual Verification:

- Confirm `setup-uv@v6` (not `@v3`) is pinned in `setup-backend/action.yml`.
- Confirm opencv apt package list in `setup-backend/action.yml` matches `Dockerfile:31-43` exactly.
- Confirm `deploy-railway/action.yml` does NOT call `railway config apply`.
- Confirm `run-acceptance-tests/action.yml` does NOT pass any `AUTH0_*` or `GOOGLE_API_KEY` env (the dev-mode globalSetup needs none).

**Implementation Note**: Pause for manual confirmation before proceeding.

---

## Phase 4: CI Workflow

### Overview

Write `.github/workflows/ci.yml` — the PR gate. Triggers on `pull_request: [opened, synchronize]`. Chained jobs via `needs:`: lint → (be-unit ∥ fe-unit) → system. Cancel in-progress on new push to the same PR.

### Changes Required:

#### 1. `ci.yml`

**File**: `.github/workflows/ci.yml`

**Intent**: Block PR merges unless lint passes, both unit suites pass, and the system suite passes. Sequential suite-level ordering with parallelism within the unit stage.

**Contract**: Workflow file with:
- `on: pull_request: types: [opened, synchronize]`.
- `concurrency: { group: ci-pr-${{ github.event.pull_request.number }}, cancel-in-progress: true }`.
- `permissions: { contents: read }`.
- Jobs:
  - `lint`: checkout + setup-backend + setup-frontend + lint-check.
  - `be-unit`: `needs: lint` + checkout + setup-backend + run-be-tests.
  - `fe-unit`: `needs: lint` + checkout + setup-frontend + run-fe-tests.
  - `system`: `needs: [be-unit, fe-unit]` + checkout + setup-backend + run-system-tests.
- No `environment:` key (no secrets needed).
- No deploy job (CD owns that).

### Success Criteria:

#### Automated Verification:

- `ci.yml` parses as valid YAML.
- `actionlint` (if available) passes.
- Branch-protection rule check: visual confirm PRs against master require `lint`, `be-unit`, `fe-unit`, `system` to pass (configured in GitHub repo settings, not in the workflow file — document as a manual setup step in Phase 7).

#### Manual Verification:

- Open a test PR against master; confirm all four jobs run in the correct dependency order.
- Push a new commit to the open PR; confirm the in-flight run is cancelled and a new one starts.
- Confirm no `RAILWAY_*` secrets are referenced (CI must not need them).

**Implementation Note**: Pause for manual confirmation before proceeding.

---

## Phase 5: CD Workflow

### Overview

Write `.github/workflows/cd.yml` — the deploy pipeline. Triggers on `push: master`. Chained jobs: lint → (be ∥ fe) → system → acceptance → deploy. The deploy job uses `environment: production` to gate `RAILWAY_TOKEN` access. Never cancel a mid-deploy.

### Changes Required:

#### 1. `cd.yml`

**File**: `.github/workflows/cd.yml`

**Intent**: On every master merge, run the full chain then auto-deploy to Railway. The deploy is automatic (no human approval click) per user decision — rollback is the recovery path.

**Contract**: Workflow file with:
- `on: push: branches: [master]`.
- `concurrency: { group: cd-master, cancel-in-progress: false }` — **never cancel mid-deploy**.
- `permissions: { contents: read }`.
- Jobs:
  - `lint`, `be-unit`, `fe-unit`, `system` — same shape as CI.
  - `acceptance`: `needs: [be-unit, fe-unit, system]` + checkout + setup-backend + setup-frontend + run-acceptance-tests.
  - `deploy`: `needs: [acceptance]` + `environment: production` + checkout + deploy-railway, with `env:` mapping `RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}`, `RAILWAY_PROJECT_ID: ${{ vars.RAILWAY_PROJECT_ID }}`, `RAILWAY_ENVIRONMENT_ID: ${{ vars.RAILWAY_ENVIRONMENT_ID }}`.
- `environment: production` on the deploy job is the GitHub Actions protection gate — even with the token in `secrets`, the job can't read it unless the environment allows.

### Success Criteria:

#### Automated Verification:

- `cd.yml` parses as valid YAML.
- `actionlint` (if available) passes.
- Visual confirm: every job in the chain has a `needs:` arrow landing in `deploy` (no parallel job can bypass `deploy`'s upstream).

#### Manual Verification:

- Push a trivial commit to master; confirm the full chain runs end-to-end.
- Trigger a second push while the first is mid-deploy; confirm the in-flight run is NOT cancelled (`cancel-in-progress: false`).
- Confirm `deploy` does not start unless `acceptance` (and therefore all upstream) passes.
- Confirm `deploy` cannot read `RAILWAY_TOKEN` if the GitHub `production` environment is misconfigured (i.e., the `environment:` key is doing real work).

**Implementation Note**: Pause for manual confirmation before proceeding.

---

## Phase 6: Railway IaC

### Overview

Write `.railway/railway.ts` declaring the single-service Free-tier topology. The human provisions the project + Volume + Bucket + secrets manually FIRST (Phase 7), then runs `railway config apply` once to reconcile the IaC (Phase 8). Thereafter CI deploys via `railway up` only.

### Changes Required:

#### 1. `.railway/railway.ts`

**File**: `.railway/railway.ts` (new)

**Intent**: Declare the full prod topology so future resource drift is caught by `config apply`. Secrets are `preserve()` placeholders — the human sets the values in dashboard; the IaC never sees them.

**Contract**: TypeScript file using `railway/iac` exports (`defineRailway`, `service`, `volume`, `bucket`, `group`, `project`, `preserve`, `github`). Conforms to the research draft at `research.md:190-303` with these adjustments for the Railpack pivot:
- Volume `data`: `region: "europe-west4"`, `sizeMB: 512` (Free-tier cap, no Live Resize).
- Bucket `uploads`: `region: "ams"` (co-locates with europe-west4 compute).
- `web` service: `source: github("krkruk/target-o-meter", { branch: "master" })`, `start: sh -c "python src/manage.py migrate --noinput && python src/manage.py qcluster & exec gunicorn src.target_o_meter.wsgi:application --bind 0.0.0.0:8000 --workers 1"` (note: no `uv run` prefix — Railpack venv is activated; verify on first deploy), `replicas: { "europe-west4": 1 }`, `volumeMounts: { "/data": data }`, `healthcheck: { path: "/health" }` (the Phase 1 view).
- `BUILDER: "railpack"` in the service env block — overrides Railway's Dockerfile auto-detection since the root `Dockerfile` is retained.
- Env block (`env:`) with:
  - Literals: `PYTHONPATH=/app`, `DEBUG=False`, `SECURE_COOKIES=True`, `USE_S3=True`, `AWS_S3_ADDRESSING_STYLE=auto`, `VISION_DETECTOR=google`, `Q2_WORKERS=1`, `APP_BASE_URL=https://target-o-meter.up.railway.app` (edit post-deploy to real domain), `BUILDER=railpack`, `RAILPACK_DJANGO_APP_NAME=target_o_meter.wsgi`.
  - `preserve()` for every secret the human provisions: `GOOGLE_API_KEY`, `OWNER_SUB_ID`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, `AUTH0_DOMAIN`, `SECRET_KEY`, `AUTH0_SECRET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`.
- Explicitly NOT set: `DEV_AUTH_BYPASS_SUB`, `DEV_ADMIN_*`, `MOCK_DETECTOR_*`, `OLLAMA_*`, `DJANGO_VITE_DEV_MODE`, `TOM_ENV_FILE`, `RAILWAY_VOLUME_MOUNT_PATH` (auto-injected), `AWS_S3_ENDPOINT_URL` (unset for Tigris per `settings.py:363` — verify in Phase 8).
- `domains: ["target-o-meter.up.railway.app"]` — replace post-deploy or drop to use Railway's auto-generated domain.
- A `group("App", [web, data, uploads])` and `project("target-o-meter", { resources: [app] })`.

#### 2. `.railway/` directory marker

**File**: N/A (directory creation)

**Intent**: The `.railway/` directory must exist before `railway config apply` works against this file.

**Contract**: Directory contains only `railway.ts`. No other artifacts.

### Success Criteria:

#### Automated Verification:

- `.railway/railway.ts` parses as valid TypeScript (if a typecheck tool is configured; otherwise visual review against the research draft).
- `railway config validate` (if the CLI exposes a dry-run validation; verify availability — if not, skip).
- `uv run python src/manage.py check` passes (no settings change here, but sanity check).

#### Manual Verification:

- Visual diff of `.railway/railway.ts` against `research.md:190-303`: every preserve() present, every literal correct, no secret values written.
- Confirm `BUILDER=railpack` is in the env block.
- Confirm `healthcheck: { path: "/health" }` references the Phase 1 view.
- Confirm `start` command has `--workers 1` (not 3) and uses the `src.target_o_meter.wsgi:application` path.
- Confirm no `preDeployCommand` (migrate is in `start`).

**Implementation Note**: Pause for manual confirmation before proceeding.

---

## Phase 7: Manual Provisioning Checklist

### Overview

This phase has no code artifacts. It is an explicit, enumerated list of every manual action the human must take in the Railway + GitHub dashboards before Phase 8's first deploy can succeed. **The agent does not execute any of these steps.** The plan lists them so the human can check them off; the agent only verifies after-the-fact that the corresponding non-secret metadata (project ID, environment ID) is wired into GitHub.

### Changes Required:

#### 1. Railway dashboard provisioning

**File**: N/A (operational)

**Intent**: Create the project + environment + Volume + Storage Bucket that the IaC references. These must exist before Phase 8's `railway config apply`.

**Contract**: The human performs each step in the Railway dashboard (or via the Railway CLI authenticated against their own account — never via the agent's MCP token):
1. Create project `target-o-meter` in region `europe-west4`.
2. Inside the project, confirm the `production` environment exists (default).
3. Attach a persistent Volume at mount path `/data`, region `europe-west4`, size 512 MB.
4. Create a Storage Bucket named `uploads` in region `ams`.
5. From the bucket's Connect panel, copy `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME` (used below).

#### 2. Railway service env vars (secrets — set by human, preserved by IaC)

**File**: N/A (operational)

**Intent**: Set every production secret in the Railway service env UI. These are the `preserve()` placeholders from Phase 6 — the IaC declares them but never sets values; the human sets values here.

**Contract**: Set each of these in the Railway dashboard (NEVER via agent MCP):
- `SECRET_KEY` and `AUTH0_SECRET`: generate locally with `openssl rand -hex 32`; set both to the SAME value.
- `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, `AUTH0_DOMAIN`: from Auth0 dashboard (`https://<tenant>.eu.auth0.com/`). Add prod callback `${APP_BASE_URL}/bff/callback` to Allowed Callback URLs.
- `GOOGLE_API_KEY`: from Google AI Studio (`https://aistudio.google.com/apikey`).
- `OWNER_SUB_ID`: chicken-and-egg — deploy once with it empty (W001 warns, Owner role inert), log in as the intended Owner, copy the WARN-logged `sub`, redeploy with it set.
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`: from the Railway Storage Bucket's Connect panel (step 1.5 above).

#### 3. Railway service env vars (non-secret — declared by IaC, verified here)

**File**: N/A (operational)

**Intent**: Confirm the IaC's non-secret literals land correctly after Phase 8's first `config apply`. The human does NOT set these manually — `config apply` will set them from `.railway/railway.ts`. Listed here for traceability.

**Contract**: After Phase 8 first `config apply`, verify in dashboard that: `DEBUG=False`, `SECURE_COOKIES=True`, `USE_S3=True`, `VISION_DETECTOR=google`, `Q2_WORKERS=1`, `BUILDER=railpack`, `RAILPACK_DJANGO_APP_NAME=target_o_meter.wsgi`, `APP_BASE_URL=https://target-o-meter.up.railway.app` (or the real domain).

#### 4. Railway CLI token (scoped, project-only)

**File**: N/A (operational)

**Intent**: Generate the scoped `RAILWAY_TOKEN` that the CD workflow will use. Must be project-scoped with Deploy + Variables read/edit; NO project delete, NO billing.

**Contract**: Railway → Account Settings → API Tokens → New Token. Scope to `target-o-meter` project. Grant Deploy + Variables read/edit. Copy the token (used in step 5 below).

#### 5. GitHub secrets + vars

**File**: N/A (operational; configured in GitHub repo Settings)

**Intent**: Wire the CD workflow's env references. `RAILWAY_TOKEN` is a secret; `RAILWAY_PROJECT_ID` and `RAILWAY_ENVIRONMENT_ID` are non-secret vars.

**Contract**: In GitHub repo Settings:
- Under `Secrets and variables → Actions → Secrets`: add `RAILWAY_TOKEN` (value from step 4).
- Under `Secrets and variables → Actions → Variables`: add `RAILWAY_PROJECT_ID` (from Railway project Settings → General), `RAILWAY_ENVIRONMENT_ID` (from Railway CLI `railway environment` or the dashboard URL).
- Under `Environments`: create the `production` environment (so `environment: production` on the deploy job resolves). No required-reviewers configured (auto-deploy posture).

#### 6. GitHub branch protection (manual; not in any workflow file)

**File**: N/A (operational)

**Intent**: Make the CI status checks load-bearing for PR merges.

**Contract**: In GitHub repo Settings → Branches → Branch protection rules for `master`: require `lint`, `be-unit`, `fe-unit`, `system` status checks pass before merge; require branches up to date before merge; require 1 approval (or whatever the team's review posture is — out of plan scope to decide).

### Success Criteria:

#### Automated Verification:

- None — this phase is operational. No code, no automated check.

#### Manual Verification:

- Human confirms each numbered step above is complete.
- After step 1: project, environment, Volume, Bucket all visible in Railway dashboard.
- After step 2: every secret in the env-var list has a non-empty value in the Railway dashboard (but the values are NOT pasted into the plan, the IaC, or any agent-visible surface).
- After step 5: GitHub `production` environment exists; `RAILWAY_TOKEN`, `RAILWAY_PROJECT_ID`, `RAILWAY_ENVIRONMENT_ID` are set in the right scopes (secret vs var).
- After step 6: branch protection rule is active and lists the four CI jobs as required checks.

**Implementation Note**: Pause for manual confirmation that every step in this phase is complete before proceeding to Phase 8. Phase 8 will fail loudly if any of these are missing.

---

## Phase 8: First Deploy Verification

### Overview

The human runs `railway config apply` once to reconcile the IaC, then triggers the first deploy. This phase is the structured checklist for the four "verify-on-first-apply/deploy" open questions plus the Free-RAM headroom check. Each item has a concrete action if it fails.

### Changes Required:

#### 1. Apply the IaC once (manual)

**File**: N/A (operational; run by the human locally with Railway CLI authenticated)

**Intent**: Reconcile `.railway/railway.ts` against the Railway project. The `preserve()` placeholders protect manually-set secrets from being clobbered.

**Contract**: From the repo root, after `railway login` and `railway link` against the production environment: `railway config apply`. Verify the command exits 0 and the dashboard shows the env block updated (literals set, secrets preserved). **If bucket creds (`AWS_*`) get clobbered by this step** (Open Q #3 — the SDK's `preserve()` semantics are unconfirmed), re-set them manually in the dashboard and add an explicit ref in the IaC for the next apply.

#### 2. Trigger the first deploy

**File**: N/A (operational)

**Intent**: Get the app running on Railway Free tier. Two paths: (a) push a trivial commit to master and let CD do it, OR (b) run `railway up` locally against the linked env. (a) is preferred — it exercises the actual CD pipeline.

**Contract**: Deploy completes (build green; service reaches active). If build fails on Python 3.14 precompiled-binary availability, set `MISE_PYTHON_COMPILE=1` in the service env (Railpack documented fallback) and redeploy.

#### 3. Verify-on-deploy checklist

**File**: N/A (operational; documented inline)

**Intent**: Close each of the four open verify-on-apply/deploy unknowns with a concrete action. **Each item is a Success Criterion below.**

**Contract**: For each of the four items + the Free-RAM check, perform the verification and apply the fallback if it fails. Document outcomes in the change.md or a deploy log.

### Success Criteria:

#### Automated Verification:

- `railway config apply` exits 0.
- `railway status` shows the service active (not crash-looping).
- `railway logs --build` shows the build succeeded (Railpack build green).
- `railway logs` shows gunicorn started, qcluster started, `/health` reachable.

#### Manual Verification:

- **Bucket creds preserved** (Open Q #3): after `config apply`, dashboard shows `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME` still populated. **Action if fails**: re-set in dashboard; add `uploads.env.AWS_*` ref to IaC for next apply.
- **Tigris endpoint reachable** (Open Q #5): submit a test upload via the SPA; the q2 task completes without an S3 connection error in logs. **Action if fails**: set `AWS_S3_ENDPOINT_URL` (preserve) to the endpoint shown in the bucket connection details.
- **Image size ≤ 4 GB** (Open Q #9): `railway status` or the dashboard shows image size. **Action if fails**: trim the image (audit railpack.json build steps; ensure `node_modules` is removed post-build like the Dockerfile does; `--no-cache-dir` if applicable).
- **Cold-start wake latency** (Open Q #11): wait for the service to sleep (idle threshold — confirm in dashboard Settings → Sleep), then curl the URL; measure time-to-200. **Action if unacceptable**: this is the documented Free-tier UX tradeoff for ≤10 users; the escape hatch is upgrading to Hobby ($5/mo, sleep off).
- **Free-RAM headroom** (the contingency check): `railway logs` after a CV job submission shows no OOM-kill. **Action if OOM**: bump `Q2_WORKERS=2` (still within PRD cap of 3); if still OOM, upgrade to Hobby ($5/mo) which raises the RAM cap to 48GB and disables the constraint entirely.
- **`/health` returns 200** (Phase 1 view works in prod): `curl https://<railway-domain>/health` returns `ok`.
- **`OWNER_SUB_ID` chicken-and-egg**: first login as the intended Owner; copy the WARN-logged `sub`; set `OWNER_SUB_ID` in dashboard; redeploy; second login confirms Owner role active.

**Implementation Note**: This phase is the operational close-out. Each manual verification item that fails has a documented action; nothing here blocks the plan from being marked complete once every item is either verified OR its fallback action is taken.

---

## Testing Strategy

### Unit Tests:

- Phase 1's `/health` view: a small pytest checking the view returns 200 with body `ok` and no DB access. Co-locate with existing Django view tests if a pattern exists; otherwise add to `src/target_o_meter/tests/` (or follow the existing test layout).
- Phase 1's `Q_CLUSTER` env-overridable change: a one-line assertion that `os.environ['Q2_WORKERS']` is respected (can fold into a settings-test if one exists; otherwise visual review suffices — the change is one line).

### Integration Tests:

- CI workflow (Phase 4): the act of opening a PR and seeing the chain run green IS the integration test. No additional test code needed.

### System Tests:

- Phase 4 CI's `system` job and Phase 5 CD's `system` job both run the existing `tests/system/` suite — already covered.
- Phase 5 CD's `acceptance` job runs the existing `src/frontend/tests-acceptance/` Playwright suite — already covered.

### Manual Testing Steps:

1. Phase 1: `make dev` still works with 3 q2 workers (default unchanged).
2. Phase 2: visual review of `railpack.json` against railpack.com docs.
3. Phase 4: open a throwaway PR; confirm 4 jobs run in dependency order.
4. Phase 5: push to master; confirm full chain + auto-deploy.
5. Phase 7: complete every numbered dashboard step (this IS the test).
6. Phase 8: walk every verify-on-deploy checklist item.

## Performance Considerations

- **Free-tier RAM budget** (`research.md:508-521`): the binding constraint. `Q2_WORKERS=1` + gunicorn `--workers 1` + opencv-python-headless import is estimated ~250-400MB against the 512MB cap. Phase 8 verifies; Hobby is the documented escape hatch.
- **Cold-start latency** is accepted for ≤10 users (`research.md:567-582`); no external ping mitigation (would defeat the RAM savings).
- **CI runtime**: each composite runs checkout + setup; the uv cache (`actions/cache@v4` keyed on `uv.lock`) and npm cache make the second-run overhead minimal. Expected wall-clock: lint ~1-2min, unit ~3-5min, system ~5-8min, acceptance ~8-12min. Chained jobs with parallel unit stage keeps total CD under ~20min including deploy.
- **Railway build time** under Railpack: first build is the slowest (no cache; potential `MISE_PYTHON_COMPILE=1` adds 5-10min if precompiled 3.14 binary missing). Subsequent builds cache on `uv.lock` + `package-lock.json` hashes.

## Migration Notes

- **Deleting `uat.yml` removes the Python UAT runner.** The `tests/acceptance/` directory has no test file yet, so nothing actually runs today (`uat.yml:19` gates on `vars.UAT_ENABLED == 'true'` which is unset). When a real UAT test file lands, re-add a UAT job that consumes the new `setup-backend` + `setup-frontend` composites; do NOT restore the old standalone workflow.
- **No DB migration.** SQLite on the Volume is untouched by this change; the only DB-relevant change is migrate-in-start (already the case under the Dockerfile path; Railpack preserves it via the start command override).
- **No rollback story needed for the Django changes.** `/health` view is additive; `Q2_WORKERS` is backward-compatible (default 3 if unset).
- **Railpack-vs-Dockerfile drift risk**: the Dockerfile (kept for local dev) and `railpack.json` (prod) now describe two different build paths. A change to one does NOT propagate to the other. Mitigation: any change to apt deps, build sequence, or start command must be reflected in BOTH files in the same PR. Document this in `AGENTS.md` as a follow-up lesson.
- **`infrastructure.md:93` Risk Register entry is now stale** — it mandates the Dockerfile path. After this plan lands, update `infrastructure.md` to reflect the Railpack-with-explicit-apt-deps mitigation.

## References

- Related research: `context/changes/infrastructure-as-code/research.md` (primary + Follow-up #1 Free-tier + Follow-up #2 decisions locked)
- Foundation: `context/foundation/infrastructure.md` (Risk Register § line 93 overridden; § line 100 PRD cap of 3 honored)
- Lessons: `context/foundation/lessons.md:5-10` (`RAILPACK_DJANGO_APP_NAME` rule — now load-bearing)
- Code refs:
  - `src/target_o_meter/settings.py:276-286` (`Q_CLUSTER` block)
  - `src/target_o_meter/settings.py:263-268` (SQLite path, `RAILWAY_VOLUME_MOUNT_PATH`)
  - `src/target_o_meter/settings.py:346-364` (`USE_S3` + Tigris)
  - `src/target_o_meter/urls.py:20-24` (no `/health` endpoint yet)
  - `src/target_o_meter/checks.py:51-141` (E001/E002/W001/W002 production guards)
  - `Dockerfile:31-43` (opencv apt list — copy into railpack.json)
  - `Dockerfile:92-125` (prod stage reference shape)
  - `pyproject.toml:5` (`requires-python>=3.14`), `pyproject.toml:2` (`name = "module1"` placeholder)
  - `.github/workflows/uat.yml` (deleted in Phase 1; was the only prior workflow)
  - `tests/system/conftest.py:82-134` (env sanitization for system tests)
  - `src/frontend/tests-acceptance/global-setup.ts:70-183` (Playwright globalSetup self-boots the stack)
- External docs (verified 2026-07-28):
  - `https://railpack.com/languages/python` (Python builder, `RAILPACK_DJANGO_APP_NAME`)
  - `https://railpack.com/guides/installing-packages` (`RAILPACK_DEPLOY_APT_PACKAGES`, `RAILPACK_PACKAGES`)
  - `https://railpack.com/deploying/railway` (Railway auto-detects Railpack)
  - `https://docs.railway.com/deploy/builds` (Railpack vs Dockerfile auto-detection)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Django Foundation Changes

#### Automated

- [x] 1.1 `uv run pytest` passes after `/health` view + `Q_CLUSTER` env change — 808da8d
- [x] 1.2 `uv run ruff check .` passes on the new view + settings edit — 808da8d
- [x] 1.3 `uv run python src/manage.py check` passes (incl. E001/E002/W001/W002) — 808da8d
- [x] 1.4 `uv run python src/manage.py migrate --no-input` applies cleanly — 808da8d
- [x] 1.5 Local curl smoke check: `curl -fsS http://127.0.0.1:8000/health` returns `ok` / 200 — 808da8d
- [x] 1.6 `Q2_WORKERS=1 uv run python src/manage.py check` passes — 808da8d

#### Manual

- [x] 1.7 `Q2_WORKERS=1 uv run python src/manage.py qcluster` boots with 1 worker — 808da8d
- [x] 1.8 `make dev` still boots with 3 workers (default unchanged) — 808da8d
- [x] 1.9 `.github/workflows/uat.yml` removed from repo root — 808da8d

### Phase 2: Railpack Configuration

#### Automated

- [x] 2.1 `railpack.json` parses as valid JSON — 1dc12a9
- [x] 2.2 Schema validation against `https://schema.railpack.com` (or visual review) — 1dc12a9
- [x] 2.3 `uv run python src/manage.py check` passes (Django still boots) — 1dc12a9
- [x] 2.4 (If Docker + BuildKit available) `railpack build .` succeeds end-to-end — SKIPPED: Docker/BuildKit + `railpack` CLI unavailable in this env; Phase 8's first Railway build is the end-to-end signal (plan explicitly permits this skip) — 1dc12a9

#### Manual

- [x] 2.5 Visual review: every required field present, apt list matches `Dockerfile:31-43` — 1dc12a9
- [x] 2.6 `RAILPACK_DJANGO_APP_NAME=target_o_meter.wsgi` present and correctly spelled — 1dc12a9
- [x] 2.7 `BUILDER=railpack` is NOT in railpack.json (lands in IaC in Phase 6) — 1dc12a9

### Phase 3: CI Composite Actions

#### Automated

- [x] 3.1 All 8 `action.yml` files parse as valid YAML — 44ef862
- [x] 3.2 `actionlint` (if available) passes on every composite — 44ef862

#### Manual

- [x] 3.3 `setup-backend/action.yml` pins `astral-sh/setup-uv@v6` (not v3) — 44ef862
- [x] 3.4 opencv apt list in `setup-backend/action.yml` matches `Dockerfile:31-43` — 44ef862
- [x] 3.5 `deploy-railway/action.yml` does NOT call `railway config apply` — 44ef862
- [x] 3.6 `run-acceptance-tests/action.yml` passes no `AUTH0_*` / `GOOGLE_API_KEY` env — 44ef862

### Phase 4: CI Workflow

#### Automated

- [x] 4.1 `ci.yml` parses as valid YAML — 946bc61
- [x] 4.2 `actionlint` (if available) passes — 946bc61

#### Manual

- [x] 4.3 Test PR runs lint → (be-unit ∥ fe-unit) → system in correct order — 1c7ff2a
- [x] 4.4 New commit on open PR cancels the in-flight run — 1c7ff2a
- [x] 4.5 No `RAILWAY_*` secrets referenced (CI is secret-free) — 1c7ff2a

### Phase 5: CD Workflow

#### Automated

- [x] 5.1 `cd.yml` parses as valid YAML — 82f4802
- [x] 5.2 `actionlint` (if available) passes — 82f4802
- [x] 5.3 Every job has a `needs:` path landing in `deploy` (no bypass) — 82f4802

#### Manual

- [ ] 5.4 Push to master runs the full chain end-to-end
- [ ] 5.5 Second push while first is mid-deploy does NOT cancel the first
- [ ] 5.6 `deploy` only starts after `acceptance` (and all upstream) passes
- [ ] 5.7 `environment: production` gate is load-bearing (token unreadable without it)

### Phase 6: Railway IaC

#### Automated

- [x] 6.1 `.railway/railway.ts` parses (typecheck or visual review) — dc762a4
- [x] 6.2 `railway config validate` (if CLI exposes dry-run validation) — dc762a4
- [x] 6.3 `uv run python src/manage.py check` passes — dc762a4

#### Manual

- [x] 6.4 Visual diff against `research.md:190-303`: every `preserve()` present, literals correct, no secret values — dc762a4
- [x] 6.5 `BUILDER=railpack` in env block — dc762a4
- [x] 6.6 `healthcheck: { path: "/health" }` references Phase 1 view — dc762a4
- [x] 6.7 `start` has `--workers 1` and `src.target_o_meter.wsgi:application` — dc762a4
- [x] 6.8 No `preDeployCommand` (migrate is in `start`) — dc762a4

### Phase 7: Manual Provisioning Checklist

#### Manual

- [x] 7.1 Railway project + environment + Volume (500MB, europe-west4, renamed web-volume→data) + Bucket (uploads, region auto) created — 844a5ae
- [x] 7.2 All secrets set on Railway web service (SECRET_KEY=AUTH0_SECRET, AUTH0_*, GOOGLE_API_KEY, AWS_STORAGE_BUCKET_NAME + AWS creds, OWNER_SUB_ID) — values NOT in plan/IaC/agent context — 844a5ae
- [x] 7.3 Scoped `RAILWAY_TOKEN` generated (project-scoped, Deploy + Variables only) — 844a5ae
- [x] 7.4 GitHub `RAILWAY_TOKEN` secret + `RAILWAY_PROJECT_ID` / `RAILWAY_ENVIRONMENT_ID` vars set — 844a5ae
- [x] 7.5 GitHub `production` environment created (no required-reviewers; auto-deploy posture) — 844a5ae
- [ ] 7.6 GitHub branch protection on master requires the 4 CI checks — DEFERRED until CI runs once (status checks populate in dropdown then); does not block first deploy
- [x] 7.7 OWNER_SUB_ID set from a known sub (chicken-and-egg N/A — owner known ahead of first deploy) — 844a5ae

### Phase 8: First Deploy Verification

#### Automated

- [ ] 8.1 `railway config apply` exits 0
- [ ] 8.2 `railway status` shows service active (not crash-looping)
- [ ] 8.3 `railway logs --build` shows Railpack build green
- [ ] 8.4 `railway logs` shows gunicorn + qcluster started, `/health` reachable

#### Manual

- [ ] 8.5 Bucket creds preserved on first apply (Open Q #3) — fallback: re-set + add explicit ref
- [ ] 8.6 Tigris endpoint reachable via test upload (Open Q #5) — fallback: set `AWS_S3_ENDPOINT_URL`
      — **MIXED. The endpoint/addressing WAS wrong then fixed (`virtual-host`→`auto`, 8024d3c): the
      upload reached S3 and `15.jpg` saved successfully at 18:11. But the SAME `15.jpg` then 403s
      on `HeadObject` after the Free→Hobby upgrade. Root cause CONFIRMED as a Railway internal
      bug (NOT our config): the plan upgrade left the storage org backing the bucket in a
      suspended state (Railway staff sam-a, station thread 3ede6443). Explains every observation:
      works-at-18:11-then-403s flip, owner 'Access denied' in the dashboard, byte-hash-matched
      creds, the 1-byte probe reproducing it. Needs Railway backend reactivation — reopen this
      row once the bucket is healthy and a real upload returns 201.**
- [ ] 8.7 Image size ≤ 4 GB (Open Q #9) — fallback: trim railpack.json build steps
- [ ] 8.8 Cold-start wake latency measured (Open Q #11) — fallback: accept or Hobby upgrade
- [ ] 8.9 Free-RAM headroom: no OOM-kill after a CV job — fallback: Q2_WORKERS=2, then Hobby
      — **NOT MET: q2 worker SIGKILLed mid-`process_image` (idle baseline already OOMs at
      512MB); user chose the Hobby ($5/mo) upgrade as the resolution (not Q2_WORKERS=2 — the
      footprint doesn't fit 512MB even at workers=1); pending the upgrade + upload retry**
- [ ] 8.10 `/health` returns 200 on the Railway domain
- [ ] 8.11 OWNER_SUB_ID set after first prod login confirms Owner role active

#### Phase 8 — Open investigation: POST /v1/scoring/jobs returns 500 (upload fails)

**Update 2026-07-30 (commits ac69636, 8024d3c):** step 1 of the investigation
— "make the error visible" — landed in `ac69636` (LOGGING dict + upload/worker
stage logging), and **step 2 confirmed + fixed the root cause** in `8024d3c`.
The traceback named it immediately: `botocore.exceptions.
InvalidS3AddressingStyleError: S3 addressing style virtual-host is invalid.
Valid options are: 'auto', 'virtual', and 'path'`. The bug was a single env
var — `AWS_S3_ADDRESSING_STYLE` declared as `"virtual-host"` in
`.railway/railway.ts`, copied verbatim from the Railway Storage Bucket's
Connect-panel "urlStyle" label, but boto3 uses a different word (`"virtual"`)
for that concept and rejected the value at client-construction time (before
any network call). The S3 creds/endpoint/ACL hypotheses were all ruled out —
the failure was a config-string vocabulary mismatch. Fix: set the value to
`"auto"` in the Railway dashboard (user, took effect on redeploy) AND in the
IaC source-of-truth (`8024d3c`, so a future `config apply` does not drift it
back). **Confirmed live after the fix:** upload pipeline runs end-to-end —
`upload received` → `upload saved stored_path=uploads/5d179f1ea2778fe3.jpg` →
`enqueued job_id=…` → `process_image: start` → `process_image: upload read
back bytes=1471270`. No more 500 on `POST /v1/scoring/jobs`.

**NEW blocker surfaced by the same logs — OOM kills the q2 worker mid-task
(Phase 8.9, sharper than the plan's contingency).** The upload now succeeds
and the task is picked up, BUT the worker running `process_image` is
SIGKILLed during `PipelineRunner.run` (opencv decode + genai import + Google
detector): the log shows `process_image: start` → `process_image: upload read
back` → `CRITICAL django-q reincarnated worker … after death`, with NO
`process_image: succeeded` and NO `Processed '…process_image'`. Scoring never
completes. Root cause is the **512MB Free-tier ceiling vs the single-container
footprint**: django-q2 at `workers=1` still spawns worker + monitor + pusher +
scheduler processes (4 q2 + 1 gunicorn all sharing 512MB), and the idle
baseline already trips gunicorn `SIGKILL! Perhaps out of memory?` BEFORE any
upload arrives (visible at 18:11:39). The opencv/genai import spike on top is
what kills the worker mid-task. `Q2_WORKERS=1` and gunicorn `--workers 1` are
already minimal; the 2-service split that would let qcluster live on a
separate box is ruled out by SQLite+ORM-broker (plan §"What We're NOT
Doing"). **User decision:** upgrade to Hobby ($5/mo, ~8GB RAM, sleep off) —
the documented escape hatch. The product flow (upload works, scoring doesn't)
is gated on it.

A regression was also caught + fixed in `ac69636`: the initial
`root: WARNING` LOGGING config filtered django-q's INFO "Q Cluster … running."
banner (logger named `django-q`, not under `django.*`), breaking
`test_qcluster_shuts_down_cleanly_on_sigint` — fixed by an explicit
`django-q` INFO logger entry.

Status as of 2026-07-29: the app is live (`/health` → 200 `ok`, OAuth login +
admin role work on PC and phone). **Image upload is the one broken flow**:
`POST /v1/scoring/jobs` returns HTTP 500. Added 2026-07-29 after first real
prod upload attempt.

**Evidence collected:**
- Railway proxy log: `POST /v1/scoring/jobs 500`, `upstreamRqDuration: ~286ms`,
  `rxBytes: 1910426` (~1.9 MB received — the upload body reached the app).
- App log: **no Traceback / no 500 entry.** Only `[Q] INFO Enqueued
  [targetometer] 10/11` from the scheduled `reap-stuck-scoring-jobs` task —
  unrelated to the failing upload (the enqueues are the reaper, not the upload).
- Failure is fast (~290ms) — consistent with a synchronous raise in the view,
  not a hung request.

**Primary hypothesis (most likely): the S3/Tigris write in `save_upload`
raises, and the traceback is invisible because `settings.py` has NO `LOGGING`
dict.** The route (`src/bff/routers/scoring_routes.py:111`) does
`storage.save_upload(file.read(), file.name)` against `ScoringStorage()`, which
under `USE_S3=True` is django-storages' S3 backend writing to Tigris. A Tigris
write failure (creds, endpoint, addressing, ACL) would raise in boto3 → 500
from `@transaction.atomic` rollback. With no `LOGGING` config, Django's prod
default does not surface the traceback where we're reading logs — so the 500
is effectively silent. This would also explain why the first real exercise of
the S3 path (every prior request was auth/static, not a bucket write) is where
it breaks.

**Secondary hypotheses (check if primary is ruled out):**
1. **Tigris addressing/endpoint mismatch.** `AWS_S3_ADDRESSING_STYLE=virtual-host`
   + `AWS_S3_ENDPOINT_URL=https://t3.storageapi.dev` were set from the bucket's
   Connect panel, but `settings.py:370-371` reads them with `.get()` — a typo
   or a virtual-host vs path-style mismatch against the actual Tigris bucket
   (`systematic-organizer-lvvddc`) would surface as an S3 connection error
   only on write. Open Q #5 / #8.6 predicted exactly this.
2. **`AWS_QUERYSTRING_AUTH` / ACL conflict.** `settings.py` sets
   `AWS_QUERYSTRING_AUTH=True` + `AWS_DEFAULT_ACL=None`; if Tigris rejects the
   upload's ACL or the presign config, the PUT fails.
3. **Form/parser rejection surfacing as 500.** Less likely (would usually be
   413/422), but `file.read()` on a large upload under the 10 MiB cap is in
   the path.

**Investigation steps (in order — do the first before changing anything):**
1. **Make the error visible.** Add a minimal `LOGGING` dict to `settings.py`
   that sends `django.request` (and a catch-all `django` + app loggers) to the
   console at `INFO`/`DEBUG`. Redeploy, retry the upload, read the actual
   Traceback from `railway logs`. This is the single highest-leverage step —
   every hypothesis above is guesswork until the traceback is visible.
2. **Once the traceback is visible**, branch on the exception type:
   - `botocore`/`boto3`/`EndpointConnectionError`/`ClientError` → Tigris
     connection or auth (hypothesis 1/2). Verify `AWS_*` values against the
     bucket Connect panel; test a write directly via a Django shell
     (`storage.save('_probe', b'x')`).
   - Django `SuspiciousOperation`/form error → hypothesis 3.
3. **Direct bucket write probe** (independent of the app): run
   `railway run python src/manage.py shell` (or a one-off) and execute
   `from django.core.files.storage import default_storage;
   default_storage.save('probe.txt', ContentFile(b'x'))`. If this fails with
   the same error, the bug is in S3 config, not the view.

**Do NOT** "fix" by disabling S3 or widening `ALLOWED_HOSTS`-style — the S3
path is load-bearing for prod storage. Fix the root cause the traceback names.

**Related Phase 8 row:** this investigation supersedes the "verify-on-failure"
stance of 8.6 (Tigris endpoint) — the upload IS the test, and it's failing.

- [ ] 8.12 (this investigation) — make the 500 traceback visible via a `LOGGING`
      dict, then fix the root cause it names; confirm a real upload returns 201
      — **traceback visible (ac69636) + root cause fixed (8024d3c: `virtual-host`→`auto`):
      upload now returns success and the task is enqueued/started (no more 500). The "confirm
      201" half is done in spirit but the row stays open because `process_image` doesn't
      *complete* — it's OOM-killed (8.9); final confirmation of a SUCCEEDED job pending the
      Hobby upgrade**
