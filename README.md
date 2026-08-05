# Target-o-meter

[![CI](https://github.com/krkruk/target-o-meter/actions/workflows/ci.yml/badge.svg)](https://github.com/krkruk/target-o-meter/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
![Python 3.14+](https://img.shields.io/badge/Python-3.14+-blue)
![Django 6](https://img.shields.io/badge/Django-6-green)

<p align="center">
  <img src="./docs/hello.png" alt="Target-o-meter — score your shooting targets with computer vision" width="720" />
</p>

**Target-o-meter** scores shooting targets from a photo. Upload a picture of a
target, an asynchronous computer-vision pipeline detects the shot holes, and the
ISSF line-break rule turns them into a score (0–10, plus "X" for center hits).
Accept the result and it lands on your personal score dashboard.

Supports the two ISSF pistol target types:

- **10m Air Pistol** — 170 × 170 mm target
- **25m / 50m Precision Pistol** — 550 × 550 mm target

---

## How it works

1. **Sign in** via OAuth (Auth0) — the app stores only the provider `sub` ID, never your email.
2. **Upload** a photo of your target through the web wizard.
3. **Async CV pipeline** (OpenCV + LangChain, queued via django-q2) detects holes and rectifies the target geometry.
4. **ISSF scoring** — each hole is scored 0–10, with the line-break rule (a hole touching a higher-value ring counts as the higher value) and "X" for bullseye hits.
5. **Review** the marked-up image + per-hole scores, then **accept** the result.
6. **Dashboard** aggregates your accepted results — totals, averages, best score, daily trend.

---

## Run locally

There are two paths: **bare metal** (full HMR dev experience) or **containers** (prod-shape, no host toolchain needed). In both cases the app boots in **dev mode without Auth0 and without CV/LLM credentials** — see [Dev without Auth0 / CV creds](#dev-without-auth0--cv-creds).

### Prerequisites

- **Python ≥ 3.14** and [`uv`](https://docs.astral.sh/uv/) (the project's package manager)
- **Node.js** + **npm** (for the React/Vite frontend)
- **`make`**
- For the container path: **`podman`** (5.8+) with `podman-compose`. Podman is Docker-compatible, so `docker compose` works too if you alias it — the compose files are unchanged.

### Bare metal (with HMR)

```bash
# 1. Install backend deps
uv sync

# 2. Install frontend deps
cd src/frontend && npm install && cd ../..

# 3. Configure env
cp .env.example .env          # then edit — at minimum set DEV_AUTH_BYPASS_SUB (see below)

# 4. Run Django (:8000) + Vite (:5173) + qcluster (async worker), with HMR
make dev
```

Open <http://localhost:8000>.

> `make dev` runs three processes concurrently (Django, the Vite dev server for the SPA, and the django-q2 worker). `Ctrl-C` stops all three.

### Containers

The dev container stack brings up **web + worker + MinIO (S3) + a bucket-creator**, live-reloading, exercising the S3 storage backend against MinIO.

> **`.env` is required** for the container targets. Unlike the bare-metal path
> (where Django's `load_dotenv()` silently no-ops on a missing file), the
> container targets pass `--env-file .env` to compose so the repo-root `.env`
> is loaded the same way under podman-compose and docker-compose v2. If `.env`
> is absent, compose refuses to start the stack with an env-file-not-found
> error. Fresh clones must `cp .env.example .env` first.

```bash
cp .env.example .env          # required — see note above
make dev-container            # podman compose --env-file .env -f docker/docker-compose.dev.yml up --build
```

For a **prod-shape** stack locally (`DEBUG=false`, built frontend served via WhiteNoise, gunicorn):

```bash
make prod-container           # podman compose --env-file .env -f docker/docker-compose.prod.yml up --build
```

---

### Dev without Auth0 / CV creds

The app is designed to run locally **without** an Auth0 tenant and **without**
Google/Ollama vision credentials. Two dev-only switches in `.env` make this work
(both require `DEBUG=True`, which the dev targets set automatically):

| Variable | What it does |
|---|---|
| `DEV_AUTH_BYPASS_SUB` | When set, every request is authenticated as this user — **no Auth0 needed**. To log in as the Owner, set this to the same value as `OWNER_SUB_ID`. The app refuses to boot if this is set while `DEBUG=False`. |
| `VISION_DETECTOR=mock` | The default detector emits a deterministic-random hole pattern, so the upload → poll → result round-trip works **without** `GOOGLE_API_KEY` or Ollama. Set `VISION_DETECTOR=google` (or `ollama`) for the real CV pipeline. |

So a minimal dev `.env` is:

```ini
DEBUG=True
DEV_AUTH_BYPASS_SUB=dev-me          # log in as this user (no Auth0)
OWNER_SUB_ID=dev-me                 # optional: make that user the Owner
VISION_DETECTOR=mock                # no CV/LLM creds needed
```

**Auth0 is required** for the real OAuth login flow in any non-dev environment.
Create an Auth0 application and fill in the `AUTH0_CLIENT_ID`,
`AUTH0_CLIENT_SECRET`, `AUTH0_DOMAIN`, and `AUTH0_SECRET` vars in `.env`
(see [`.env.example`](./.env.example) for the full list and notes).

---

## Environment variables

The full list with inline notes lives in [`.env.example`](./.env.example). What
follows is the minimum you must set, split by environment. A "mandatory" var is
one the app either **refuses to boot without** (boot-time `E0xx` system check or
a `KeyError` from `os.environ[...]`) or that is **required for the feature to
function** (e.g. OAuth). Optional vars have a safe default.

### Mandatory for dev

These four are all you need to run `make dev` with no Auth0 tenant and no CV
creds. Everything else in `.env.example` either has a safe default or is
prod-only.

| Variable | Why it's mandatory | Example |
|---|---|---|
| `DEBUG=True` | Set automatically by `make dev` / `make run`. Enables HMR via django-vite, permissive `ALLOWED_HOSTS`, and the dev-auth-bypass. **E001 refuses to boot** if `DEV_AUTH_BYPASS_SUB` is set while `DEBUG=False`. | `True` |
| `DEV_AUTH_BYPASS_SUB` | Authenticates every request as this user — **no Auth0 needed** in dev. Set it equal to `OWNER_SUB_ID` to log in as the Owner. | `dev-me` |
| `OWNER_SUB_ID` | The single source of truth for the Owner role. Optional in dev (W001 only warns), but needed if you want to exercise owner-only routes. Set equal to `DEV_AUTH_BYPASS_SUB` to be Owner locally. | `dev-me` |
| `VISION_DETECTOR=mock` | The default in `.env.example`. Emits a deterministic-random hole pattern so upload → poll → result works **without** `GOOGLE_API_KEY` / Ollama. Set `google` or `ollama` for the real CV pipeline. | `mock` |

> `SECRET_KEY` / `AUTH0_SECRET` and `AUTH0_*` are **not** mandatory in dev —
> `SECRET_KEY` falls back to a hardcoded insecure key, and the dev-bypass
> middleware makes Auth0 optional when `DEBUG=True`.

### Mandatory for prod

These are enforced: the app either fails a boot-time system check (`E001`/`E002`)
or raises a `KeyError` reading the env var directly.

| Variable | Enforcement | Why |
|---|---|---|
| `DEBUG=False` | — | Prod posture. E001 blocks boot if `DEBUG=False` while `DEV_AUTH_BYPASS_SUB` is set. |
| `SECRET_KEY` (or `AUTH0_SECRET`) | **E002** refuses to boot | Must not be the `django-insecure-…` fallback. Generate with `openssl rand -hex 32`. Also signs the session cookie + CSRF tokens. |
| `APP_BASE_URL` | Derives `ALLOWED_HOSTS` when `ALLOWED_HOSTS` is unset | The canonical deploy URL (e.g. `https://your-app.example.com`). |
| `AUTH0_CLIENT_ID` | Required for the OAuth flow to function | Auth0 OIDC client. |
| `AUTH0_CLIENT_SECRET` | Required for the OAuth flow to function | Auth0 OIDC client. |
| `AUTH0_DOMAIN` | Required for the OAuth flow to function | e.g. `your-tenant.eu.auth0.com`. |
| `OWNER_SUB_ID` | **W001** warns (boots, but Owner role is inert until set) | The `sub` that resolves to the Owner role. Set after the first login logs its `sub` at `WARNING`. |

### Prod storage — mandatory **only if `USE_S3=True`**

When `USE_S3=True`, `settings.py` reads these with `os.environ[...]` (not
`.get`), so a missing value is a **loud `KeyError` at boot**, not a silent
misconfiguration.

| Variable | Notes |
|---|---|
| `AWS_ACCESS_KEY_ID` | Tigris (Railway Storage Buckets) creds in prod; `minioadmin` in the dev container. |
| `AWS_SECRET_ACCESS_KEY` | As above. |
| `AWS_STORAGE_BUCKET_NAME` | Your Railway Storage Bucket name (`target-o-meter-local` in the dev container). |
| `AWS_S3_ENDPOINT_URL` | **Unset** for Tigris/Railway prod. `http://minio:9000` (compose) / `http://localhost:9000` (host) for MinIO. |
| `AWS_S3_ADDRESSING_STYLE` | `auto` (default) for Tigris; `path` for MinIO. |

### Optional (both dev and prod)

| Variable | Default | Notes |
|---|---|---|
| `ALLOWED_HOSTS` | Derived from `APP_BASE_URL` | Comma-separated; explicit override. Empty (permissive) when `DEBUG=True`. |
| `SECURE_COOKIES` | `False` | Sets the `SECURE` flag on session + CSRF cookies. **`True` in prod** (behind HTTPS). Also enables `SECURE_PROXY_SSL_HEADER`. |
| `DJANGO_VITE_DEV_MODE` | Defaults to `DEBUG` | Native `make dev` HMR leaves it unset. The dev container sets it `False` (no Vite service — serves the baked bundle). |
| `Q2_WORKERS` | `3` | django-q2 worker count (AGENTS.md §2 cap is 3). Railway Free tier narrows to `1` to fit the 512 MB RAM budget. |
| `GOOGLE_API_KEY` | — | Required only when `VISION_DETECTOR=google` (the prod CV detector). |
| `OLLAMA_HOST` / `OLLAMA_MODEL` | — | Required only when `VISION_DETECTOR=ollama` (local LLM detector). |
| `MOCK_DETECTOR_HOLE_COUNT` / `MOCK_DETECTOR_SEED` | `10` / random | Tune/determinize the mock detector (`VISION_DETECTOR=mock` only). |
| `DEV_ADMIN_SUB` / `DEV_ADMIN_PASSWORD` | — | Seeded Django admin login. **Dev-only** — never set in prod. |
| `TOM_ENV_FILE` | — | Point `load_dotenv()` at a non-default `.env` path (e.g. a secrets-manager mount). |
| `RAILWAY_VOLUME_MOUNT_PATH` / `STATIC_ROOT` | `BASE_DIR` / `BASE_DIR/staticfiles` | Where the SQLite DB and collected statics live. |

---

## Makefile cheat sheet

The `Makefile` is the source of truth for common tasks. Run `make help` to see
all targets.

| Command | Description |
|---|---|
| `make dev` | Django (:8000) + Vite (:5173) + qcluster, with HMR (native dev) |
| `make run` | Django dev server only (no Vite — SPA JS won't load) |
| `make prod` | Boot Django in `DEBUG=false` against the built + collected bundle |
| `make migrate` | Apply Django migrations |
| `make collectstatic` | Build the frontend + collect hashed static assets |
| `make dev-container` | Containerized dev stack (web + worker + MinIO). Requires a repo-root `.env` (`cp .env.example .env` first). |
| `make prod-container` | Containerized prod-shape stack (gunicorn, WhiteNoise). Requires a repo-root `.env` (`cp .env.example .env` first). |
| `make check` | Lint + type-check + import contracts (backend + frontend) — the verification gate |
| `make be-test` | `check` + backend pytest |
| `make fe-test` | `check` + frontend vitest |
| `make system-test` | System tests (`tests/system/`) |

---

## Architecture

A monolithic **Domain-Driven Design** app with a **Backend-For-Frontend** (BFF) layer.

```
src/
├── target_o_meter/         # Django settings, ASGI/WSGI, root URLs
├── frontend/               # React + Vite SPA
├── bff/                    # HTTP layer: django-ninja routers, OAuth, orchestration
└── domains/                # Bounded contexts — pure logic, zero HTTP
    ├── identity/           # OAuth/OIDC, sub→UUID mapping, roles
    ├── vision/             # OpenCV + LangChain CV pipeline, ISSF scoring (q2 tasks)
    └── core/               # Uploads, chart plotting
```

**Strict boundaries** keep domains isolated: no cross-domain ORM imports, no HTTP
inside domains, DTOs only across boundaries, UUID links instead of foreign keys,
and BFF workflows wrapped in `transaction.atomic()`. An [import-linter](./.importlinter)
contract enforces domain independence in CI.

- **Async CV** runs via django-q2 against a SQLite broker, capped at **3 concurrent** processing tasks.
- **Database:** SQLite (WAL mode).
- **Storage:** local filesystem in dev (`USE_S3=False`), MinIO in the container stack, S3/Tigris (Railway Storage Buckets) in prod.
- **Identity:** OAuth 2.0 / OIDC via Auth0. The only stored identifier is the provider `sub`. The Owner role is derived by matching `sub` against `OWNER_SUB_ID` — never persisted. Sessions are Django encrypted `HttpOnly` cookies (BFF pattern).

---

## Tech stack

- **Backend:** Django 6, django-ninja (Pydantic DTO contracts), django-q2, django-vite
- **Computer vision:** OpenCV, LangChain (Google Gemini / Ollama)
- **Frontend:** React 18 + react-router-dom + Vite (state is plain `useState` — no Redux/Oval; charts via `recharts`, cookie consent via `vanilla-cookieconsent`)
- **Auth:** Auth0 (OAuth 2.0 / OIDC)
- **Package manager:** `uv` (PEP 735 dependency groups)
- **Storage:** django-storages S3 backend (MinIO / Tigris), `FileSystemStorage` dev fallback

---

## Testing & quality

- `make check` runs ruff (lint + autofix), import-linter (architecture contracts), and the frontend type-check — the gate every commit passes.
- `make be-test` / `make fe-test` run the backend (pytest) and frontend (vitest) suites.
- Tests are organized by the V-Model: domain unit/integration tests co-locate with their domains, system tests live in [`tests/system/`](./tests/system), acceptance/E2E in [`tests/acceptance`](./tests/acceptance).
- The suite is risk-driven — see [`context/foundation/test-plan.md`](./context/foundation/test-plan.md) for the named risks each test targets.

---

## Contributing

1. Branch from `master`.
2. One-time setup: `git config core.hooksPath .githooks` — this installs a `pre-commit` hook that runs the full CI cycle (`make check` + pytest + vitest) before a commit lands. Use `git commit --no-verify` to bypass deliberately for WIP commits.
3. Ensure `make check` is green before pushing.

See [`AGENTS.md`](./AGENTS.md) for the full system architecture and development rules.

---

## Automated pull-request review

Every pull request is reviewed automatically by
[`reviewer-target-o-meter`](https://github.com/krkruk/reviewer-target-o-meter) —
a companion LLM-agent tool that reads the PR diff and posts a structured
implementation review as a comment: findings anchored to **file + line**,
classified by **severity** (CRITICAL / WARNING / OBSERVATION) and **dimension**
(correctness, security, maintainability, testability, performance, design,
documentation). The review follows a three-lens methodology (plan drift /
safety-quality-pattern / test-coverage) mapped to those seven dimensions, so it
produces a focused signal rather than a generic issue sweep.

It is **read-and-flag only** — the exit code is advisory (`0` clean / `1`
findings) and the workflow runs it under `continue-on-error`, so it **never
blocks a merge**.

The integration lives in [`.github/workflows/review.yml`](./.github/workflows/review.yml):
it fires on every `pull_request`, installs the tool + [`ast-grep`](https://ast-grep.github.io)
(which backs the structural search), and runs it against the checked-out PR. It
requires the `OPENROUTER_API_KEY` secret in the repo — see the workflow file for
the full env-var mapping, and the reviewer project's README for the methodology,
cost model, and local-run instructions.

---

## License

[MIT](./LICENSE) © 2026 Krzysztof Kruk
