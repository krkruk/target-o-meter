---
date: 2026-07-28T18:32:59Z
researcher: opencode (research sub-agents)
git_commit: e72c4b230bd5056f445e13dae946d75034fdc1de
branch: master
repository: krkruk/target-o-meter
topic: "GitHub Actions CI/CD pipeline (modular composite actions) + Railway Infrastructure-as-Code (.railway/railway.ts) + env var provisioning catalog"
tags: [research, codebase, github-actions, ci-cd, railway, infrastructure-as-code, django-q2, sqlite, tigris, composite-actions]
status: complete
last_updated: 2026-07-28
last_updated_by: opencode
last_updated_note: "Added follow-up #2: three decisions locked — (1) cold starts accepted for ≤10 users + documented, (2) Q_CLUSTER narrowed to Q2_WORKERS=1 (env-overridable, IaC updated), (3) CD acceptance = JS Playwright E2E in dev mode (mock detector, zero secrets/cost). See Follow-up Research #2. Prior: Free-tier constraint, 0.5 GB RAM cap, IaC adjusted (volume 512MB, gunicorn 1 worker)."
---

# Research: GitHub Actions CI/CD + Railway IaC + env var provisioning

**Date**: 2026-07-28T18:32:59Z
**Researcher**: opencode (4 parallel research sub-agents)
**Git Commit**: [`e72c4b2`](https://github.com/krkruk/target-o-meter/commit/e72c4b230bd5056f445e13dae946d75034fdc1de)
**Branch**: `master` (pushed → GitHub permalinks stable)
**Repository**: [`krkruk/target-o-meter`](https://github.com/krkruk/target-o-meter)

## Research Question

Build a GitHub Actions CI/CD pipeline for a **public** repo on GitHub's **free** runners. Requirements:

1. **Modular** infrastructure code — some components shared across workflows, some custom. (Decided: composite actions under `.github/actions/<name>/action.yml`.)
2. **CI workflow** — triggers on `pull_request` opened + `synchronize` (new commit on an existing PR). Runs sequentially: BE unit+integration → FE unit+integration → system tests.
3. **CD workflow** — triggers on push to `master` (PR merge). Runs: BE unit+integration → FE unit+integration → system tests → acceptance tests → Railway CLI deploy.
4. **Railway IaC script** declaring required resources per [`infrastructure.md`](../../../foundation/infrastructure.md). (Decided: 2-service `web`+`worker` topology as primary, **single-service documented as contingency**.)
5. Identify every environment variable so the user can provision the ones that "must still be retrieved from the platform".

## Summary

### What's straightforward
- The repo already has valuable prior art: `.github/workflows/uat.yml` pins Python 3.14, `astral-sh/setup-uv@v3` (stale — bump to `@v6`), `uv sync --all-groups`, and maps all 12 Auth0 UAT secrets.
- The four test suites are **env-hermetic**: BE unit/integration, FE vitest, and `tests/system/` all run with **zero GitHub secrets** (no Auth0, no S3, no real detector — system tests use `VISION_DETECTOR=mock` + a sanitized env at [`tests/system/conftest.py:82`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/tests/system/conftest.py#L82)). Only the Auth0 UAT suite needs secrets.
- The only system-dep landmine is OpenCV: `opencv-python-headless` needs `libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1` installed via apt **before any test that imports `cv2`** ([`Dockerfile:31-43`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/Dockerfile#L31)). The existing `uat.yml` does NOT install these — a latent bug any vision test would trip.
- The frontend has **no build-time env vars** (no `VITE_*`, no `import.meta.env`). One fewer thing to wire.

### ⚠️ HEADLINE FINDING — the requested 2-service Railway topology is BROKEN

The user asked for `web` + `worker` as two Railway services sharing one Volume as the **primary**, with single-service as the **contingency**. The research inverts this: **the 2-service topology cannot work with this codebase as-is.**

Two independent fatal reasons:

1. **django-q2 uses the SQLite DB as its broker.** [`settings.py:284`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/settings.py#L284) sets `'orm': 'default'` — the q2 broker table lives in the app DB, and the `process_image` task body reads/writes `ScoringJob` rows there ([`vision/services.py:142,226`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/domains/vision/services.py#L142)).
2. **A Railway Volume attaches to exactly ONE service.** The worker cannot mount `web`'s volume, and SQLite has no network listener. So the worker either has no DB (every task → `DoesNotExist`) or a *separate* empty DB file (same result). This also makes [`infrastructure.md:97`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/context/foundation/infrastructure.md#L97)'s "concurrent writes corrupt SQLite" risk certain rather than Low.

➡️ **The only working shapes for SQLite + django-q2 on Railway are:**
- **(A) Single-service** — gunicorn + qcluster in one container sharing one Volume. **This becomes the recommended primary.**
- **(B) Migrate to Postgres** — network-accessible to both services. Deviates from [`infrastructure.md`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/context/foundation/infrastructure.md)'s "no managed DB" cost decision (~+$6-38/mo).

➡️ **Frame this before `/10x-plan`**: the user must pick (A) or (B) before the IaC draft is finalizable. The 2-service draft is included below (annotated `⚠️ BROKEN`) per the original ask, but should NOT ship.

### What to ship
- Two workflows (CI on PR, CD on master) built from **8 composite actions**.
- `.railway/railway.ts` for the **single-service** topology (the working one), with the 2-service draft retained as a documented target for the Postgres-migration future.
- A complete env-var catalog with the exact "provision from platform" list (Auth0, Google AI Studio, Railway token + project/env IDs, Tigris bucket creds, generated SECRET_KEY, chicken-and-egg OWNER_SUB_ID).

## Detailed Findings

### 1. Test suite invocation — what each suite actually needs in CI

| Suite | Command | Secrets? | System deps | Boots app? |
|---|---|---|---|---|
| BE unit+integration | `uv run pytest` ([`Makefile:136`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/Makefile#L136)) | **None** — all settings have safe dev defaults ([`settings.py:63-119`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/settings.py#L63)) | opencv apt libs (import in `tests/test_geometry_regression.py`) | No (pytest-django manages `src/db.sqlite3`, [`settings.py:263-268`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/settings.py#L263)) |
| FE unit+integration | `cd src/frontend && npm run test` (= `vitest run`, [`package.json:11`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/frontend/package.json#L11)) | **None** — API is mocked ([`App.test.tsx:10-13`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/frontend/src/App.test.tsx#L10)) | None (Node only) | No (jsdom) |
| System | `uv run pytest tests/system` ([`Makefile:146`](https://github.com/krzruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/Makefile#L146)) | **None** — conftest sanitizes env + forces `VISION_DETECTOR=mock` ([`conftest.py:82-134`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/tests/system/conftest.py#L82)) | opencv apt libs | **Yes — spawns `manage.py runserver` subprocess** ([`conftest.py:209-323`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/tests/system/conftest.py#L209)); uses `httpx`, **no browser** |
| Acceptance (Python UAT) | `RUN_UAT=1 uv run pytest tests/acceptance -m uat` ([`Makefile:155`](https://github.com/krzruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/Makefile#L155)) | **All 12 Auth0 UAT secrets** ([`uat.yml:42-53`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/.github/workflows/uat.yml#L42)) | opencv + Chromium | TBD — **no test file exists yet**, only conftest scaffolding ([`tests/acceptance/conftest.py:1-9`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/tests/acceptance/conftest.py#L1)) |
| Acceptance (JS E2E) | `cd src/frontend && npm run test:acceptance` (= `playwright test`, [`package.json:13`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/frontend/package.json#L13)) | **None** — dev-bypass middleware, hardcoded mock env ([`global-setup.ts:82-100`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/frontend/tests-acceptance/global-setup.ts#L82)) | opencv + Chromium | **Yes — `globalSetup` boots Django + Vite + qcluster itself** ([`global-setup.ts:70-183`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/frontend/tests-acceptance/global-setup.ts#L70)) |

**Critical clarifications surfaced:**

- **"Acceptance tests" is ambiguous in this repo.** There are TWO acceptance surfaces:
  1. **Python UAT** at `tests/acceptance/` — real Auth0, secret-gated, **no test file yet** (deferred).
  2. **JS Playwright E2E** at `src/frontend/tests-acceptance/` — 4 real spec files (`scoring-flow`, `marked-image-load`, `accept-flow`, `dashboard-viewport`), dev-bypass, **no secrets**.
  - Per the user's decision ("acceptance always runs in CD on master merge"), the CD workflow should run **the JS E2E suite** (real specs, no secrets needed). The Python UAT suite stays in the existing `uat.yml` (no file to run yet anyway). The user should confirm which surface they meant.

- **`make check` mutates files** — `ruff check --fix` ([`Makefile:117`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/Makefile#L117)) writes autofixes. In CI this is harmless (no commit) but the `[2/5]` re-check ([`Makefile:119`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/Makefile#L119)) is the actual gate. Treat `make check` (ruff + import-linter + FE tsc) as the **first stage** of both workflows — it's the cheapest fast-fail.

- **Sequential vs parallel**: nothing forces literal sequential steps in one job. Each suite is env-isolated with its own DB. **Recommended: chained jobs via `needs:`** (cleaner failure isolation, natural parallelism if you later drop the chain). The user's "sequentially" requirement is satisfied by `needs:` just as well as by sequential steps.

- **Node version is unpinned** — no `.nvmrc`, no `engines`, no Volta. Pin via `actions/setup-node@v4` with `node-version: 20` (LTS, newer than the Dockerfile's Debian-bundled Node 18).

### 2. Composite-action structure

**Prior art** (`.github/workflows/uat.yml`): `actions/checkout@v4`, `actions/setup-python@v5` (py 3.14), `astral-sh/setup-uv@v3` (**stale — bump to @v6**), `uv sync --all-groups`, `uv run playwright install --with-deps chromium`. Repo guard: `github.repository == 'krkruk/target-o-meter'` ([`uat.yml:19`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/.github/workflows/uat.yml#L19)). No `.github/actions/` dir exists; no `CODEOWNERS`; no `dependabot.yml`.

**Proposed tree** (all under `.github/actions/<name>/action.yml`):

```
.github/actions/
├── setup-backend/action.yml       # apt opencv libs + setup-python 3.14 + setup-uv@v6 + uv sync --frozen --all-groups + uv cache
├── setup-frontend/action.yml      # setup-node@v4 (node 20, npm cache) + npm ci  (input: working-directory=src/frontend)
├── lint-check/action.yml          # = make check (ruff check --fix, ruff check, lint-imports, npm run lint, npx tsc --noEmit)
├── run-be-tests/action.yml        # uv run pytest              (input: pytest-extra="")
├── run-fe-tests/action.yml        # cd src/frontend && npm run test   (vitest run)
├── run-system-tests/action.yml    # uv run pytest tests/system
├── run-acceptance-tests/action.yml # uv run playwright install --with-deps chromium + npx playwright install chromium + cd src/frontend && npm run test:acceptance
└── deploy-railway/action.yml      # curl -fsSL cli.new | sh + railway config apply + railway up  (secrets: railway-token, railway-project-id, railway-environment-id)
```

**Job-graph recommendation** (CI):

```
lint (ruff + lint-imports + FE tsc) ─┬─> be-unit   (uv run pytest)
                                     └─> fe-unit   (npm run test) ──┐
be-unit ───────────────────────────────────────────────────────────┼─> system (uv run pytest tests/system)
fe-unit ───────────────────────────────────────────────────────────┴─> acceptance (npm run test:acceptance, needs both)
```

The user spec says "sequential"; this graph preserves the fail-fast ordering (lint → unit → system) while enabling parallelism. If strict sequential is required, collapse into one job with `uses:` in order.

**Concurrency:**
- **CI (PR)**: `concurrency: { group: ci-pr-${{ github.event.pull_request.number }}, cancel-in-progress: true }` — new push to an open PR cancels the in-flight run.
- **CD (master)**: `concurrency: { group: cd-master, cancel-in-progress: false }` — **never cancel a mid-deploy** (`railway up` returns when build is *triggered*, not complete; cancelling races Railway's builder).

**Workflow skeletons:**

```yaml
# .github/workflows/ci.yml
name: CI
on:
  pull_request:
    types: [opened, synchronize]
concurrency:
  group: ci-pr-${{ github.event.pull_request.number }}
  cancel-in-progress: true
permissions: { contents: read }
jobs:
  pipeline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/setup-backend
      - uses: ./.github/actions/setup-frontend
      - uses: ./.github/actions/lint-check
      - uses: ./.github/actions/run-be-tests
      - uses: ./.github/actions/run-fe-tests
      - uses: ./.github/actions/run-system-tests
```

```yaml
# .github/workflows/cd.yml
name: CD
on:
  push:
    branches: [master]
concurrency:
  group: cd-master
  cancel-in-progress: false
permissions: { contents: read }
jobs:
  build-test-deploy:
    runs-on: ubuntu-latest
    environment: production   # GitHub required-env gate for RAILWAY_* secrets
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/setup-backend
      - uses: ./.github/actions/setup-frontend
      - uses: ./.github/actions/lint-check
      - uses: ./.github/actions/run-be-tests
      - uses: ./.github/actions/run-fe-tests
      - uses: ./.github/actions/run-system-tests
      - uses: ./.github/actions/run-acceptance-tests
      - uses: ./.github/actions/deploy-railway
        env:
          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
          RAILWAY_PROJECT_ID: ${{ vars.RAILWAY_PROJECT_ID }}
          RAILWAY_ENVIRONMENT_ID: ${{ vars.RAILWAY_ENVIRONMENT_ID }}
```

**Note on `environment: production`**: GitHub Actions protection environments gate secret access. `RAILWAY_TOKEN` should live under the `production` environment (not repo-level), so even a rogue workflow on master can't exfiltrate it without the env gate.

### 3. Railway IaC — the headline inversion

**Decisions baked from research:**

- **Build path = Dockerfile, NOT Railpack.** [`infrastructure.md:93`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/context/foundation/infrastructure.md#L93) (Risk Register) explicitly mandates "use Dockerfile deploy path instead of Railpack auto-detection" to avoid OpenCV build failures. Railway auto-uses a root `Dockerfile` whose default (last) stage is `prod` ([`Dockerfile:92`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/Dockerfile#L92)). → **`RAILPACK_DJANGO_APP_NAME` does NOT apply** (the [`lessons.md:5-10`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/context/foundation/lessons.md#L5) rule is Railpack-only; the Dockerfile `CMD` already uses the full WSGI path at [`Dockerfile:125`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/Dockerfile#L125)).
- **Region**: Volume uses `europe-west4` (friendly name for `europe-west4-drams3a`); Bucket uses `ams` (Amsterdam — co-locates with EU West Metal; valid per Railway's `sjc|iad|ams|sin` region set, immutable after creation).
- **Volume holds ONLY SQLite.** Uploads + deliverables → Tigris bucket; static → baked into image ([`Dockerfile:119`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/Dockerfile#L119)); `MEDIA_ROOT` unset/unused in prod.
- **`RAILWAY_VOLUME_MOUNT_PATH` is auto-injected** by Railway from the `volumeMounts` mount path → do NOT set it manually in the env block ([`settings.py:266`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/settings.py#L266)).
- **Migrate runs in `start`, NOT `preDeployCommand`.** Railway volumes are **not mounted during pre-deploy** → migrate must run post-mount against `/data/db.sqlite3`. Use `sh -c "uv run ... migrate --noinput && exec uv run gunicorn ..."`.
- **No `/health` endpoint exists** ([`urls.py:20-24`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/urls.py#L20)). Either omit `healthcheck` (Railway uses process liveness; no zero-downtime readiness signal) or add a trivial `/health` view first. **Recommended: add the view** during implementation.
- **Worker healthcheck: OMIT.** qcluster has no HTTP listener; probing → connection refused → unhealthy redeploy loop.

**Bucket credential wiring — honest uncertainty:** the Railway IaC DSL reference shows `db.env.DATABASE_URL` for *databases* but does **not** document `bucket().env.AWS_*`. Safe path: provision the bucket once (dashboard or MCP `railway_create_bucket`), let Railway inject the creds, then `preserve()` them in `.railway/railway.ts` so `config apply` never clobbers them. If the SDK exposes `uploads.env.AWS_*`, prefer that — flagged for verification on first apply.

#### RECOMMENDED `.railway/railway.ts` (single-service — the working one)

```ts
import {
  bucket,
  defineRailway,
  github,
  group,
  preserve,
  project,
  service,
  volume,
} from "railway/iac";

export default defineRailway((ctx) => {
  const prod = ctx.environment === "production";

  // SQLite volume. Region = friendly name for europe-west4-drams3a (Amsterdam).
  // 512 MB on Free/Trial (both cap Volume storage at 0.5 GB — see Follow-up
  // Research §Free-tier limits). SQLite MVP footprint is <100 MB, so 512 MB is
  // ~5x headroom. NOTE: Live Resize is Hobby+ only — pick the right size up
  // front on Free; you cannot grow it without upgrading.
  const data = volume("data", {
    region: "europe-west4",
    sizeMB: 512,
  });

  // Tigris-backed object storage for uploads + 3 deliverables per job.
  // Region "ams" co-locates with europe-west4 compute. IMMUTABLE after creation.
  const uploads = bucket("uploads", { region: "ams" });

  // Shared prod env. The q2 task body reads S3 creds, GOOGLE_API_KEY,
  // VISION_DETECTOR, AUTH0 — the worker and web share this (single container).
  const env = {
    PYTHONPATH: "/app", // project not editable-installed (pyproject name "module1");
    //                gunicorn + qcluster subprocesses need /app on sys.path
    //                to import src.* (docker-compose.prod.yml:23-24, 58-60).
    DEBUG: "False", // E001/E002/W002 guards depend on this (checks.py).
    SECURE_COOKIES: "True", // Railway terminates TLS → SECURE cookie flags +
    //                       SECURE_PROXY_SSL_HEADER (settings.py:223-251).
    USE_S3: "True", // flip STORAGES['default'] to Tigris (settings.py:346-358).
    AWS_S3_ADDRESSING_STYLE: "auto", // Tigris default (settings.py:364).
    VISION_DETECTOR: "google", // prod detector (requires GOOGLE_API_KEY).
    // Q2_WORKERS: narrow django-q2 to 1 worker (Free-tier RAM budget, ≤10
    //   users). REQUIRES the settings.py change in /10x-plan (Q_CLUSTER['workers']
    //   = int(os.environ.get("Q2_WORKERS", "3"))). Inert until that lands; the
    //   local default stays 3 so `make dev` is unaffected. See Follow-up #2.
    Q2_WORKERS: "1",

    // Secrets — provision once in dashboard, preserve() keeps them across applies.
    GOOGLE_API_KEY: preserve(), // Google AI Studio
    OWNER_SUB_ID: preserve(), // copy from first prod login WARN (W001 if empty)
    AUTH0_CLIENT_ID: preserve(),
    AUTH0_CLIENT_SECRET: preserve(),
    AUTH0_DOMAIN: preserve(), // e.g. your-tenant.eu.auth0.com
    SECRET_KEY: preserve(), //  = same value as AUTH0_SECRET (E002 blocks the
    AUTH0_SECRET: preserve(), //    insecure fallback under DEBUG=False).

    // Tigris bucket creds. Safe path: provision bucket once (dashboard /
    // railway_create_bucket) → Railway injects → preserve() keeps them.
    // IF the SDK exposes bucket creds as refs, prefer:
    //   uploads.env.AWS_ACCESS_KEY_ID, etc. (analogous to db.env.*).
    AWS_ACCESS_KEY_ID: preserve(),
    AWS_SECRET_ACCESS_KEY: preserve(),
    AWS_STORAGE_BUCKET_NAME: preserve(),

    // NOTE: AWS_S3_ENDPOINT_URL intentionally OMITTED (unset for Tigris per
    // settings.py:363 + .env.example:104). VERIFY on first deploy — if boto3
    // can't reach Tigris, set AWS_S3_ENDPOINT_URL (preserve) to the endpoint
    // shown in the bucket connection details.

    // Explicitly NOT set in prod (E001 guard; dev-only):
    //   DEV_AUTH_BYPASS_SUB, DEV_ADMIN_*, MOCK_DETECTOR_*, OLLAMA_*,
    //   DJANGO_VITE_DEV_MODE (omit → defaults to DEBUG=False → manifest mode),
    //   TOM_ENV_FILE (no .env file in prod).
    // Explicitly NOT set (Railpack-only, inert under Dockerfile deploy):
    //   RAILPACK_DJANGO_APP_NAME
    // Auto-injected by Railway from volumeMounts (do NOT set manually):
    //   RAILWAY_VOLUME_MOUNT_PATH
  };

  const web = service("web", {
    source: github("krkruk/target-o-meter", { branch: "master" }),
    // Build path: Dockerfile (NOT Railpack) — satisfies infrastructure.md:93.
    // Migrate runs in START (not preDeploy): volumes are NOT mounted during
    // pre-deploy, so migrate must run post-mount against /data/db.sqlite3.
    // qcluster is backgrounded; gunicorn is foreground (exec) for clean SIGTERM.
    //
    // FREE-TIER RAM BUDGET: Free plan caps RAM at 0.5 GB/service (Trial 1 GB).
    //   --workers 1 keeps gunicorn's resident footprint minimal (each worker
    //   ~40-60 MB). --workers 3 (the docker-compose.prod.yml default) is a
    //   Hobby-tier shape. See Follow-up Research §Budget math.
    start:
      'sh -c "uv run python src/manage.py migrate --noinput && ' +
      "uv run python src/manage.py qcluster & \" && " +
      "exec uv run gunicorn src.target_o_meter.wsgi:application " +
      '--bind 0.0.0.0:8000 --workers 1"',
    // No healthcheck yet: no /health endpoint exists (urls.py:20-24). Add a
    // /health view before enabling healthcheck. Port 8000 = gunicorn bind.
    replicas: { "europe-west4": 1 }, // single replica (volumes forbid >1; Free plan also caps at 1).
    env: {
      ...env,
      APP_BASE_URL: "https://target-o-meter.up.railway.app", // ← EDIT to real domain post-deploy.
      //   ALLOWED_HOSTS derives from this host (settings.py:92-99). OAuth
      //   redirect URI also uses it.
    },
    domains: ["target-o-meter.up.railway.app"], // ← replace; or drop to use Railway's auto-generated domain.
    volumeMounts: { "/data": data }, // RAILWAY_VOLUME_MOUNT_PATH=/data auto-injected → DB=/data/db.sqlite3.
  });

  const app = group("App", [web, data, uploads]);

  return project("target-o-meter", { resources: prod ? [app] : [app] });
});
```

> **Production-grade alternative to `sh -c "... & exec gunicorn"`**: install `supervisord` (or `s6-overlay`) as PID 1 and let it supervise gunicorn + qcluster. Cleaner signal handling, cleaner logs, survives the "backgrounded child dies when shell exits" class of bugs. For MVP the `sh -c` form is the minimal working start; flag supervisord as a fast-follow.

#### The 2-service draft (⚠️ BROKEN — retained for the Postgres-migration future)

```ts
// ⚠️ BROKEN FOR SQLITE + django-q2 ORM broker. The worker service below will
// not function because it cannot mount web's Railway Volume (1 volume → 1
// service), and SQLite has no network listener. qcluster's process_image
// does ScoringJob.objects.get(id=...) against the broker DB (the app DB) and
// will raise DoesNotExist for every task web enqueues. Use this shape ONLY
// after migrating the app DB to Postgres (network-accessible to both services).
const web = service("web", {
  source: github("krkruk/target-o-meter", { branch: "master" }),
  start:
    'sh -c "uv run python src/manage.py migrate --noinput && exec uv run gunicorn src.target_o_meter.wsgi:application --bind 0.0.0.0:8000 --workers 3"',
  replicas: { "europe-west4": 1 },
  env: { ...env, APP_BASE_URL: "https://target-o-meter.up.railway.app" },
  domains: ["target-o-meter.up.railway.app"],
  volumeMounts: { "/data": data },
});

const worker = service("worker", {
  source: github("krkruk/target-o-meter", { branch: "master" }),
  start: "uv run python src/manage.py qcluster", // no migrate here (only web migrates)
  replicas: { "europe-west4": 1 },
  // No healthcheck: qcluster has no HTTP listener; Railway uses process liveness.
  env, // no APP_BASE_URL (worker doesn't serve HTTP)
  // No volumeMounts: a Railway volume cannot be shared with web. ← the root cause.
});
```

**To make 2-service work, you'd need to**: (1) `railway add --database postgres`, (2) add `psycopg` + `dj-database-url` to deps, (3) repoint `DATABASES` to the Postgres URL in prod, (4) remove the Volume from the IaC. Cost: +$6-38/mo per [`infrastructure.md`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/context/foundation/infrastructure.md). This is a meaningful scope change — surface it as an open question, don't do it silently.

### 4. Environment variable catalog

#### Bucket A — App runtime vars (PROD, set on Railway via IaC)

| Name | Purpose | Secret? | Source / Provisioning |
|---|---|---|---|
| `SECRET_KEY` | Django session/CSRF signing (alias; `AUTH0_SECRET` wins if both set) | YES | **Generate** `openssl rand -hex 32` |
| `AUTH0_SECRET` | Canonical session-cookie + CSRF signing (preferred) | YES | **Generate** (same value as `SECRET_KEY`) |
| `DEBUG` | Must be `False` in prod (E001/E002/W002 guards) | no | IaC literal: `False` |
| `APP_BASE_URL` | Deploy URL; derives `ALLOWED_HOSTS` + OAuth redirect | no | IaC literal: the Railway domain (edit post-deploy) |
| `SECURE_COOKIES` | Cookie SECURE flags + `SECURE_PROXY_SSL_HEADER` (Railway TLS) | no | IaC literal: `True` |
| `AUTH0_CLIENT_ID` | Auth0 OIDC client ID | no* | **Auth0 dashboard** |
| `AUTH0_CLIENT_SECRET` | Auth0 OIDC client secret | YES | **Auth0 dashboard** |
| `AUTH0_DOMAIN` | Auth0 tenant, e.g. `your-tenant.eu.auth0.com` | no | **Auth0 dashboard** |
| `OWNER_SUB_ID` | `sub` → Owner role (derived, never persisted). Empty → fail-closed (W001) | no | **Chicken-and-egg** — copy from first prod login WARN log |
| `USE_S3` | Flip `STORAGES['default']` to Tigris. Loud `KeyError` if `AWS_*` missing | no | IaC literal: `True` |
| `AWS_ACCESS_KEY_ID` | Tigris access key (`os.environ[...]` = loud fail) | YES | **Tigris/Railway Storage Bucket output** |
| `AWS_SECRET_ACCESS_KEY` | Tigris secret key | YES | **Tigris/Railway Storage Bucket output** |
| `AWS_STORAGE_BUCKET_NAME` | Tigris bucket name | no | **Tigris/Railway Storage Bucket output** |
| `AWS_S3_ADDRESSING_STYLE` | `auto` for Tigris, `path` for MinIO | no | IaC literal: `auto` |
| `VISION_DETECTOR` | `google` (prod) / `mock` / `ollama` | no | IaC literal: `google` |
| `GOOGLE_API_KEY` | Google AI Studio (Gemini) key | YES | **Google AI Studio** |
| `PYTHONPATH` | `/app` (project not editable-installed) | no | IaC literal: `/app` |
| `RAILWAY_VOLUME_MOUNT_PATH` | SQLite dir (`<path>/db.sqlite3`) | no | **Auto-injected by Railway** from volume mount — do NOT set |

\*`AUTH0_CLIENT_ID` is conventionally treated as a secret in CI ([`uat.yml:50`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/.github/workflows/uat.yml#L50)).

**Prod NOT set (explicitly dev-only)**: `DEV_AUTH_BYPASS_SUB` (E001 blocks boot), `DEV_ADMIN_*`, `AWS_S3_ENDPOINT_URL` (MinIO-only, must be UNSET for Tigris), `DJANGO_VITE_DEV_MODE` (must be unset → manifest mode), `MOCK_DETECTOR_*`, `OLLAMA_*`, `TOM_ENV_FILE`.

#### Bucket B — App runtime vars (DEV only, in `.env`, NOT prod)

`DEV_AUTH_BYPASS_SUB`, `DEV_ADMIN_{SUB,NICK,PASSWORD}`, `DJANGO_VITE_DEV_MODE`, `AWS_S3_ENDPOINT_URL`, `OLLAMA_{HOST,MODEL}`, `MOCK_DETECTOR_{HOLE_COUNT,SEED}`, `TOM_ENV_FILE`. See [`.env.example`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/.env.example).

#### Bucket C — Frontend build-time vars (`VITE_*`)

**None.** No `import.meta.env` / `VITE_*` usage anywhere in `src/frontend/`. The SPA resolves all config at runtime via the BFF API. (If added later, they must be present at `npm run build` in BOTH the Dockerfile prod stage and any CI that builds the FE.)

#### Bucket D — CI secrets (GitHub Secrets)

Already wired in [`uat.yml:42-53`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/.github/workflows/uat.yml#L42): `AUTH0_UAT_{USER,OWNER}_{EMAIL,PASSWORD,SUB,NICK}` (8), plus `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, `AUTH0_DOMAIN`, `OWNER_SUB_ID` (shared with prod). The future CD workflow additionally needs `RAILWAY_TOKEN`.

#### Bucket E — CI non-secret vars (GitHub `vars.*`)

- `UAT_ENABLED` — gates the UAT job ([`uat.yml:19`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/.github/workflows/uat.yml#L19)).
- **Future**: `RAILWAY_PROJECT_ID`, `RAILWAY_ENVIRONMENT_ID` (opaque UUIDs, safe as `vars.*`).

#### Bucket F — Railway CLI / deploy tokens (CD job)

| Name | Secret? | Provisioning |
|---|---|---|
| `RAILWAY_TOKEN` | YES | Railway → **Account Settings → API Tokens → New Token**. Scope to this project, grant Deploy + Variables read/edit. **No** project delete / billing. |
| `RAILWAY_PROJECT_ID` | no | Railway → project Settings → General → Project ID (UUID) |
| `RAILWAY_ENVIRONMENT_ID` | no | `railway environment` (CLI linked) or dashboard URL |

**Order of creation matters (chicken-and-egg)**: the Railway project + environment + Volume + Storage Bucket must exist BEFORE you can retrieve `RAILWAY_PROJECT_ID`, `RAILWAY_ENVIRONMENT_ID`, or the Tigris `AWS_*` creds. Create them via dashboard or `railway init`/`railway add` first, then populate GitHub vars/secrets.

#### Bucket G — Test-only vars (never in prod, never in `.env`)

`RUN_UAT` ([`conftest.py:39`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/conftest.py#L39)), `UAT_BASE_URL`, `TOM_FIXTURE_IMG`, `SPA_{DJANGO,VITE}_PORT`, `SPA_ACCEPTANCE_BASE_URL`, `SERVICE_ROLE`, `DJANGO_SETTINGS_MODULE`, compose-internal `MINIO_ROOT_{USER,PASSWORD}`.

### "Must provision from platform" — exact retrieval steps

1. **Auth0** (`https://<tenant>.eu.auth0.com/`) — `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, `AUTH0_DOMAIN`: Applications → your app → Settings. Add prod callback `${APP_BASE_URL}/bff/callback` to *Allowed Callback URLs*. **Two real UAT users** (User + Owner) → User Management → Users → Create User → record `email`, `password`, `sub` (`auth0|...`) for each. The Owner's `sub` MUST equal `OWNER_SUB_ID`.
2. **Google AI Studio** (`https://aistudio.google.com/apikey`) — `GOOGLE_API_KEY`: Create API key.
3. **Railway** (project must exist FIRST) — `RAILWAY_TOKEN` (Account Settings → API Tokens, scoped to project), `RAILWAY_PROJECT_ID` (project Settings → General), `RAILWAY_ENVIRONMENT_ID` (CLI `railway environment` or dashboard URL). Then attach a persistent Volume at `/data`.
4. **Tigris / Railway Storage Bucket** (after project exists) — create a bucket in region `ams`; copy `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME` from the bucket's Connect panel. Leave `AWS_S3_ENDPOINT_URL` **unset** for Tigris ([`settings.py:363`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/settings.py#L363)).
5. **Generated locally** — `AUTH0_SECRET` (= `SECRET_KEY`): `openssl rand -hex 32`. `OWNER_SUB_ID`: chicken-and-egg — deploy once with it empty (W001 warns, Owner role inert), log in as the intended Owner, copy the WARN-logged `sub`, redeploy.

## Code References

**Makefile / test commands**
- [`Makefile:104-131`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/Makefile#L104) — `check` target (ruff --fix mutates files in CI; [2/5] re-check is the gate)
- [`Makefile:135-161`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/Makefile#L135) — `be-test`, `fe-test`, `system-test`, `acceptance-test` targets

**pyproject / pytest config**
- [`pyproject.toml:54-63`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/pyproject.toml#L54) — `addopts = ["--strict-markers", "--strict-config", "-m", "not uat"]`; markers `dev`/`uat`; `DJANGO_SETTINGS_MODULE`
- [`pyproject.toml:28-52`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/pyproject.toml#L28) — uv groups (`default`, `dev`, `test`, `system-test`)
- [`pyproject.toml:5`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/pyproject.toml#L5) — `requires-python = ">=3.14"`

**Django settings (env-var surface)**
- [`settings.py:263-268`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/settings.py#L263) — SQLite path: `Path(os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', BASE_DIR)) / 'db.sqlite3'`
- [`settings.py:276-286`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/settings.py#L276) — `Q_CLUSTER` ORM broker (`'orm': 'default'`) — root of the 2-service-is-broken finding
- [`settings.py:346-364`](https://github.com/krzysztofkruk/source/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/settings.py#L346) — `USE_S3` flip + `AWS_*` loud-fail (`os.environ[...]`)
- [`settings.py:63-67`](https://github.com/krzruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/settings.py#L63) — `SECRET_KEY = SECRET_KEY or AUTH0_SECRET or insecure-fallback`
- [`settings.py:223-251`](https://github.com/krzruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/settings.py#L223) — `SECURE_COOKIES` → cookie SECURE flags + `SECURE_PROXY_SSL_HEADER`

**Production guards**
- [`checks.py:51-141`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/checks.py#L51) — E001 (`DEV_AUTH_BYPASS_SUB`+`DEBUG=False` boot-block), E002 (insecure `SECRET_KEY`+`DEBUG=False` boot-block), W001 (empty `OWNER_SUB_ID`), W002 (`DEBUG=True`+non-localhost)
- [`urls.py:20-24`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/urls.py#L20) — no `/health` endpoint

**Test boot mechanisms**
- [`tests/system/conftest.py:82-134`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/tests/system/conftest.py#L82) — `_SANITIZED_ENV_DENYLIST` (scrubs all real creds)
- [`tests/system/conftest.py:209-323`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/tests/system/conftest.py#L209) — `_boot_runserver` spawns `manage.py runserver` subprocess
- [`src/frontend/tests-acceptance/global-setup.ts:70-183`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/frontend/tests-acceptance/global-setup.ts#L70) — Playwright `globalSetup` boots Django + Vite + qcluster + seed
- [`conftest.py:39`](https://github.com/krzruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/conftest.py#L39) — `RUN_UAT` gate

**System deps**
- [`Dockerfile:31-43`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/Dockerfile#L31) — opencv apt list: `libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1`
- [`Dockerfile:62`](https://github.com/krzruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/Dockerfile#L62) — `uv sync --frozen --no-dev --group default`
- [`Dockerfile:92,119,125`](https://github.com/krzruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/Dockerfile#L92) — prod stage, collectstatic at build time, gunicorn `CMD`

**Compose (prod-shape reference for Railway)**
- [`docker-compose.prod.yml:19,23-25,55,57-73`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/docker-compose.prod.yml#L19) — web gunicorn cmd, `PYTHONPATH=/app`, `RAILWAY_VOLUME_MOUNT_PATH=/data`, worker qcluster cmd, full prod env

**Frontend**
- [`src/frontend/package.json:7-16`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/frontend/package.json#L7) — scripts (`test`=`vitest run`, `test:acceptance`=`playwright test`, `lint`=`tsc --noEmit`)

**Prior art & foundation**
- [`.github/workflows/uat.yml`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/.github/workflows/uat.yml) — the only existing workflow (Python 3.14, setup-uv@v3 [stale], Auth0 secret map)
- [`context/foundation/infrastructure.md`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/context/foundation/infrastructure.md) — region pin, Risk Register (Dockerfile-over-Railpack), Operational Story
- [`context/foundation/lessons.md:5-10`](https://github.com/krzruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/context/foundation/lessons.md#L5) — `RAILPACK_DJANGO_APP_NAME` rule (Railpack-only, N/A under Dockerfile)

## Architecture Insights

1. **The "2 services on a shared Volume" mental model is a Railway-impossibility, not a config bug.** Railway's volume contract is strictly 1-volume-to-1-service (volumes guide + DSL reference). This collides with django-q2's ORM-broker design ([`settings.py:284`](https://github.com/krzruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/settings.py#L284)) when the broker DB is a file. The compose files "work" locally only because both containers bind-mount the *same host path* — a sharing primitive Railway doesn't offer.
2. **Railpack vs Dockerfile is already decided by the Risk Register.** [`infrastructure.md:93`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/context/foundation/infrastructure.md#L93) pre-baked the OpenCV-into-Dockerfile mitigation. Don't re-litigate; don't set `RAILPACK_DJANGO_APP_NAME`.
3. **The PRD's "Max 3 concurrent processing tasks" cap ([`settings.py:281`](https://github.com/krzruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/settings.py#L281)) is weakened by single-service.** A runaway CV job can starve web request-handling in the same container. Acceptable for MVP (single user, hobby scale); the natural evolution is the Postgres + 2-service split when scale demands it.
4. **Concurrency discipline**: cancel PR runs aggressively (`cancel-in-progress: true`), **never** cancel master deploys (`cancel-in-progress: false`). `railway up` returns on build-trigger, not build-complete — cancelling races Railway's builder.
5. **`make check` is the project's own gate** ([`Makefile:104-131`](https://github.com/krzruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/Makefile#L104)); `be-test`/`fe-test` depend on it. Treat it as stage 1 of both workflows even though the user's spec says "tests" — dropping the gate silently regresses the repo's own discipline.
6. **`environment: production`** on the CD job isn't cosmetic — it's the GitHub Actions mechanism that gates `RAILWAY_TOKEN` access behind a required-environment review. Use it; don't hoist the token to repo-level.

## Historical Context (from prior changes)

- `context/changes/infrastructure-as-code/change.md` — the change under which this research is filed. Status advanced `new` → `preparing` on completion of this research.
- `context/foundation/infrastructure.md:142-148` — "Out of Scope" explicitly parked "CI/CD pipeline setup (planned: GitHub Actions with auto-deploy on merge)". This change unparks it.
- `context/foundation/infrastructure.md:80-85` — Operational Story documents Railway preview deploys, secrets scoping, rollback (SQLite migrations don't auto-roll back), approval posture (no built-in gates at Hobby tier — use `environment: production` in GH Actions).
- `.github/workflows/uat.yml` — the repo's first CI file (F-01 Phase 6.5); established the Python 3.14 + `setup-uv` + Playwright install pattern reused here.
- `context/foundation/lessons.md:5-10` — the `RAILPACK_DJANGO_APP_NAME` lesson is Railpack-scenario-only and is explicitly **not** applied in this research's Dockerfile-path IaC.

## Related Research

No prior `research.md` exists under `context/changes/**/` or `context/archive/**/` for this topic. The platform decision underpinning the IaC is [`context/foundation/infrastructure.md`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/context/foundation/infrastructure.md) (the third foundation contract).

## Open Questions

1. ~~**2-service vs single-service** — needs a decision before `/10x-plan`.~~ **RESOLVED (follow-up): single-service, per user decision.** The Postgres+2-service path is documented as a future scale evolution, not an MVP option.
2. ~~**Which "acceptance" surface did the user mean for CD?**~~ **RESOLVED (follow-up #2): JS Playwright E2E in dev mode** (`src/frontend/tests-acceptance/`, `VISION_DETECTOR=mock`, zero secrets, zero Google AI cost). See Follow-up Research #2 §Decision 3.
3. **Bucket credential wiring in the IaC DSL.** Whether `uploads.env.AWS_*` works analogously to `db.env.DATABASE_URL` is **unconfirmed** by the reference. Safe path is `preserve()`; cleaner path is a direct ref. Verify on first `railway config apply` and finalize the IaC accordingly.
4. **`/health` endpoint.** No view exists ([`urls.py:20-24`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/urls.py#L20)). Recommend adding a trivial 200-returning `/health` view during implementation so Railway has a real readiness signal (and volume-attached redeploy caveats are cleaner).
5. **`AWS_S3_ENDPOINT_URL` for Tigris.** Project convention is unset ([`settings.py:363`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/settings.py#L363)), but boto3 reaching Tigris without an endpoint is non-obvious. Verify on first deploy; if it fails, set the Tigris endpoint (preserve) from the bucket connection details.
6. **`supervisord` vs `sh -c "... & exec gunicorn"` for single-service start.** The `sh -c` form is the minimal working start; supervisord/s6-overlay is the production-grade follow-up. Decide during `/10x-plan` (recommend MVP ships `sh -c`, fast-follow supervisord).
7. **`astral-sh/setup-uv@v3` → `@v6` bump.** The existing `uat.yml` uses the stale v3; the new composite should pin v6. Consider a follow-up bumping `uat.yml` too for consistency (or migrate `uat.yml` to consume the same `setup-backend` composite).
8. ~~**[Free-tier] Sleep mode acceptance.**~~ **RESOLVED (follow-up #2): cold starts ACCEPTED.** App targets ≤10 users; cold-start 502 is the balanced middle ground. Full cold-start documentation in Follow-up Research #2 §Decision 1. Escape hatch to Hobby ($5/mo, sleep off) if UX blocks or user base grows.
9. **[Free-tier] Image size ≤ 4 GB verification.** Free/Trial cap the image at 4 GB. The prod Dockerfile (Python 3.14 + opencv-headless + system libs + node + built frontend bundle) is estimated ~800 MB–1.2 GB — should fit, but **verify after the first build** via `railway status` / dashboard. If it exceeds 4 GB, trim the image (multi-stage cleanup, `--no-cache-dir`, strip node from the final stage — already done in `Dockerfile:86,113` via `rm -rf node_modules`).
10. ~~**[Free-tier] Q_CLUSTER worker count tuning.**~~ **RESOLVED (follow-up #2): narrow to `Q2_WORKERS=1`.** Make `Q_CLUSTER['workers']` env-overridable in `settings.py` (default 3 locally, Railway sets 1). IaC env block updated. See Follow-up Research #2 §Decision 2.

## Follow-up Research 2026-07-28 (Free-tier constraint)

**Trigger**: user decision to target Railway's **Free tier** first ("squeeze in the application into the free tier"), upgrade to $5 Hobby only if necessary. Single-service topology also confirmed.

### Correction to the primary research

The primary research's Summary stated Railway's free option is a "one-time $5 credit grant" (the Trial). **That's incomplete.** Railway has two free-ish tiers ([`docs.railway.com/pricing/plans`](https://docs.railway.com/pricing/plans)):

| Plan | Price | Credit | RAM/service | CPU/service | Volume storage | Image size | Retention |
|---|---|---|---|---|---|---|---|
| **Trial** | $0 (no card) | **one-time $5** | **1 GB** | 2 vCPU | 0.5 GB | 4 GB | 24 h |
| **Free** | $0/mo (recurring) | **$1/mo recurring** | **0.5 GB** | 1 vCPU | 0.5 GB | 4 GB | 24 h |
| Hobby | $5/mo | $5/mo usage included | 48 GB | 48 vCPU | 5 GB | 100 GB | 72 h |

➡️ **The user's "free tier" = the Free plan ($1/mo recurring credit, 0.5 GB RAM cap).** The Trial is the on-ramp (one-time $5, 1 GB RAM, no card) — once the Trial credit is exhausted the account drops to Free unless upgraded.

### Resource usage pricing (per month)

- **RAM: $10 / GB / month** (the dominant cost for an always-on service)
- CPU: $20 / vCPU / month (near-zero when idle)
- Volume Storage: $0.15 / GB / month
- Network Egress: $0.05 / GB

### Budget math — can the app fit the Free $1/mo credit?

$1/mo ÷ $10/GB-mo (RAM) = **0.1 GB = 100 MB of RAM running 24/7 for a month**.

Realistic resident set for this single-service app (Django + gunicorn + OpenCV import + qcluster + q2 worker subprocesses): **~250-400 MB**.

| Scenario | RAM | 24/7 cost | Fits Free $1/mo? |
|---|---|---|---|
| App awake 24/7 at 300 MB | 0.3 GB | **$3/mo** | ❌ 3× over budget |
| App awake 24/7 at 500 MB (Free cap) | 0.5 GB | $5/mo | ❌ 5× over budget |
| Sleep mode ON, awake ~2 h/day (300 MB) | avg ~25 MB | ~$0.25/mo | ✅ within $1/mo |
| Sleep mode ON, awake ~8 h/day (300 MB) | avg ~100 MB | ~$1/mo | ✅ at the wire |

➡️ **Free plan is viable ONLY with sleep mode ON**, accepting the cold-start 502 UX tradeoff documented at [`infrastructure.md:64`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/context/foundation/infrastructure.md#L64). For ~2-8 awake-hours/day the $1/mo credit covers it. For always-on, the user must upgrade to Hobby ($5/mo, sleep can be disabled).

### Free-tier feasibility verdict

**TIGHT but viable**, with these adjustments baked into the IaC above and these caveats:

1. **RAM is the binding constraint (0.5 GB cap on Free, 1 GB on Trial).** The IaC is updated:
   - gunicorn `--workers 1` (was 3) — each worker ~40-60 MB.
   - Volume `sizeMB: 512` (was 1024) — Free/Trial cap Volume storage at 0.5 GB.
2. **Single-service is even more justified.** Two services = 2× RAM billing against the same $1/mo credit. The "broken-with-SQLite" finding from the primary research now has a cost ally.
3. **Sleep mode must be ON** to stay within Free. Expect cold-start 502s on the first request after idle. Mitigation options: (a) accept it (MVP posture), (b) external health-check ping every few minutes to keep the service warm (defeats most of the sleep savings — awake ~24h/day → ~$3/mo → blows the budget), (c) upgrade to Hobby.
4. **Q_CLUSTER worker count** ([`settings.py:281`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/settings.py#L281)) hardcodes 3 workers — ~180-300 MB combined. Recommend making it env-overridable and setting `Q2_WORKERS=1` or `2` on Railway to leave headroom for gunicorn + OpenCV under the 0.5 GB cap. This is a `settings.py` change, not an IaC change → flag for `/10x-plan`.
5. **Image must stay ≤ 4 GB** (Free/Trial cap). Estimated ~800 MB-1.2 GB — verify after first build (Open Question #9).
6. **Volume Live Resize is Hobby+ only** — on Free you cannot grow the 512 MB volume without upgrading. Pick the right size up front (512 MB is the max on Free anyway).
7. **Rollback window is 24 h** (Free/Trial image retention) vs 72 h on Hobby. Faster rollback discipline required.
8. **Private Docker registry requires Pro** — irrelevant (deploy from the public GitHub repo).
9. **No card required for Trial** → ideal for the initial bring-up. The Trial's $5 + 1 GB RAM headroom is the right place to debug the first deploy; switch to Free (or Hobby) once stable.

### Recommended onboarding sequence (replaces infrastructure.md §"Getting Started" for this constraint)

1. **Start on Trial** (no card, $5 one-time, 1 GB RAM headroom) — bring up the IaC + first successful deploy + verify image size and Tigris wiring while RAM is comfortable.
2. **Enable sleep mode** before the Trial credit runs low — measure actual awake-time/day to project burn.
3. **When Trial $5 is exhausted → drop to Free** ($1/mo recurring) IF measured awake-time keeps burn under $1/mo. Else upgrade to Hobby ($5/mo).
4. **Hobby is the realistic steady state** for an app with real users (sleep-mode 502s are poor UX for a product). Treat Free as "demo / low-traffic personal use" and Hobby as "production".

### What changed in the IaC draft (diff summary)

- `volume("data", { sizeMB: 1024 → 512 })` — Free/Trial 0.5 GB cap; Live Resize unavailable on Free.
- gunicorn `--workers 3 → 1` — Free 0.5 GB RAM budget.
- Added Free-tier rationale comments inline.
- `replicas: { "europe-west4": 1 }` comment now notes Free plan also caps at 1 replica (in addition to the volumes-forbid->1 rule).

### What is NOT in the IaC (settings.py-level, deferred to /10x-plan)

- Making `Q_CLUSTER['workers']` env-overridable (e.g. `Q2_WORKERS`) so Railway can set it to 1-2.
- Adding the `/health` view (separate from the Free-tier question but needed for clean sleep/wake + redeploy).
- These belong in the implementation plan, not the IaC.

## Follow-up Research 2026-07-28 #2 (decisions locked)

**Trigger**: user locked the three open decisions — cold starts accepted (≤10 users), Q_CLUSTER narrowed to minimum, CD acceptance = JS Playwright E2E in dev mode for cost effectiveness.

### Decision 1 — Cold starts: ACCEPTED and DOCUMENTED (no Hobby upgrade for now)

Target user base: **≤10 users**. Sleep mode stays ON; cold-start 502s are the accepted MVP posture. This keeps the deploy within the Free $1/mo recurring credit (or stretches the Trial $5 across multiple months).

**Cold-start behavior (what to expect):**

| Aspect | Behavior | Source |
|---|---|---|
| **Wake trigger** | Incoming HTTP request to the Railway service URL. No HTTP traffic → service sleeps after Railway's idle threshold. | Railway platform behavior |
| **First-request UX** | The first request after idle **returns 502** (cold-start failure). The user retries; subsequent requests succeed once the container is warm. | [`infrastructure.md:64`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/context/foundation/infrastructure.md#L64): *"Serverless sleep mode returns 502 on first request… users experience cold-start failures on their first action."* |
| **Wake latency** | **Not measured** — Railway's sleep/wake docs page was unreachable during research (`docs.railway.com/deploy/sleeping` returned non-2xx). Typical PaaS cold-start range: 5-30s. **Verify post-deploy** and update this row. | Open |
| **Idle threshold** | Exact minutes-of-idle before sleep **not confirmed** from accessible docs. **Verify in Railway dashboard** (service Settings → Sleep) post-deploy. | Open |
| **qcluster on wake** | qcluster restarts with the container; in-flight CV jobs at sleep time are lost (q2 retries from the ORM broker table on next wake, [`settings.py:281`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/settings.py#L281) `retry`). Users polling `/v1/scores/...` see the job complete after wake. | Inference |

**Mitigation posture for ≤10 users**: **none required.** The population is small enough that occasional 502-on-first-hit is acceptable. Concretely:
- **DO NOT** add an external health-check ping every few minutes to keep the service warm — that keeps it awake ~24h/day → ~$3/mo RAM → **blows the Free $1/mo budget** (see Follow-up #1 §Budget math). The whole point of sleep mode is the RAM savings; pinging it away is self-defeating.
- Document the retry expectation for users (a sentence in the product README / a "warming up, retry in a moment" hint in the SPA on 502).
- If a 502 surfaces during a CV job submission, the upload is retried client-side; the async q2 task isn't lost (broker = SQLite on the Volume, survives sleep/wake).

**When to revisit**: if the user base grows past ~10 OR the 502 UX becomes a blocker → upgrade to Hobby ($5/mo) and disable sleep. That's the documented escape hatch; no code change, just a plan/settings toggle.

### Decision 2 — Q_CLUSTER workers: narrowed to minimum (1)

[`settings.py:281`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/target_o_meter/settings.py#L281) hardcodes `Q_CLUSTER['workers'] = 3`. Each q2 worker is a subprocess (~60-100 MB resident). On Free's 0.5 GB cap, 3 workers + gunicorn + OpenCV import is the single biggest RAM consumer.

**Implementation (deferred to `/10x-plan`, the IaC already declares the env var):**
- `settings.py`: change `Q_CLUSTER['workers']` to `int(os.environ.get("Q2_WORKERS", "3"))`. Local default stays 3 → `make dev` unchanged.
- IaC: `Q2_WORKERS: "1"` added to the `web` service env block (already applied above). Inert until the settings.py change lands.

**Why 1 (the minimum):**
- qcluster with 1 worker processes one CV task at a time. For ≤10 users the queue contention is negligible — CV jobs are async, users poll `/v1/scores/...`; a job waiting a few seconds for a worker is invisible to the user.
- PRD's "Max 3 concurrent processing tasks" cap ([`infrastructure.md:100`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/context/foundation/infrastructure.md#L100)) is a **maximum**, not a target. 1 ≤ 3 → the constraint stays honored.
- RAM saved: ~120-200 MB resident (dropping 2 subprocesses). Single biggest win toward fitting the 0.5 GB Free cap alongside gunicorn + OpenCV.

**Fallback (no code change if queue latency surfaces):** bump `Q2_WORKERS=2` in the IaC. Still well within the PRD cap; costs ~60-100 MB more RAM. Only upgrade to Hobby if 2 workers still isn't enough.

### Decision 3 — Acceptance in CD: JS Playwright E2E in DEV mode (mock detector, zero cost)

The CD workflow's acceptance step runs **`cd src/frontend && npm run test:acceptance`** (= `playwright test`, [`package.json:13`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/frontend/package.json#L13)). This is the **dev-mode** surface — no Google AI Studio, no Auth0, no secrets.

**Why this surface (not the Python UAT):**

| Surface | Detector | Auth0 | Secrets needed | Cost | Test files |
|---|---|---|---|---|---|
| **JS Playwright E2E** (`src/frontend/tests-acceptance/`) ✅ | `mock` ([`global-setup.ts:84`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/frontend/tests-acceptance/global-setup.ts#L84)) | dev-bypass ([`:82`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/frontend/tests-acceptance/global-setup.ts#L82)) | **None** | **$0** (no Gemini spend) | 4 real specs |
| Python UAT (`tests/acceptance/`) | real | real OAuth | 12 Auth0 UAT secrets + 2 real Auth0 users | real API + Auth0 spend | **No test file yet** (deferred) |

The Playwright `globalSetup` ([`global-setup.ts:70-183`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/frontend/tests-acceptance/global-setup.ts#L70)) self-boots the full stack with hardcoded dev env (`DEBUG=True`, `DEV_AUTH_BYPASS_SUB=auth0|playwright-acceptance`, `VISION_DETECTOR=mock`, `MOCK_DETECTOR_SEED=42`, `MOCK_DETECTOR_HOLE_COUNT=5`, `USE_S3=False`). No Google AI call, no real OAuth, no S3 spend.

**`.github/actions/run-acceptance-tests/action.yml` therefore needs:**
1. `setup-backend` already run — the globalSetup shells out to `uv run python src/manage.py {migrate, shell, runserver, qcluster}` ([`global-setup.ts`](https://github.com/krkruk/target-o-meter/blob/e72c4b230bd5056f445e13dae946d75034fdc1de/src/frontend/tests-acceptance/global-setup.ts#L70) spawns all four).
2. `setup-frontend` already run (`npm ci`).
3. Browser install: `uv run playwright install --with-deps chromium` (python group ships playwright) + `cd src/frontend && npx playwright install --with-deps chromium` (JS runner).
4. `cd src/frontend && npm run test:acceptance`. **No manual stack boot** — globalSetup handles Django (port 8187) + Vite (port 5173) + qcluster.

**Zero secrets, zero variable cost per CD run.** This is the cost-effective path the user asked for. The Python UAT stays in the existing `.github/workflows/uat.yml` (secret-gated, no test file yet) — untouched.

### IaC diff (incremental on Follow-up #1)

Added to the `web` service env block (already applied inline above):
```ts
    Q2_WORKERS: "1",  // narrow django-q2 (Free-tier RAM budget, ≤10 users).
                      // REQUIRES settings.py change in /10x-plan. Inert until then.
```

### Open Questions resolved by this follow-up

- **#2** (acceptance surface) → JS Playwright E2E, dev mode, mock detector (Decision 3).
- **#8** (sleep mode acceptance) → cold starts accepted for ≤10 users (Decision 1).
- **#10** (Q_CLUSTER tuning) → `Q2_WORKERS=1`, env-overridable, set on Railway (Decision 2).

### Remaining open questions (carry into /10x-plan)

- **#3** (bucket credential wiring — `uploads.env.AWS_*` vs `preserve()`) — verify on first `railway config apply`.
- **#4** (`/health` view — still recommended; needed for clean sleep/wake + volume-attached redeploy).
- **#5** (`AWS_S3_ENDPOINT_URL` for Tigris — verify on first deploy).
- **#6** (`supervisord` vs `sh -c "... & exec gunicorn"` — MVP ships `sh -c`, supervisord as fast-follow).
- **#7** (`setup-uv@v3` → `@v6` bump).
- **#9** (image size ≤ 4 GB verification — verify after first build).
- **NEW #11** (cold-start wake latency + idle threshold — verify post-deploy, see Decision 1 table).
