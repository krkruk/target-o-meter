# Sign-in + empty dashboard (S-01) Implementation Plan

## Overview

Ship the **first frontend code in the repo**: a React + TypeScript SPA that replaces F-01's server-rendered templates and lands the user-facing shell for slice S-01. The SPA serves two screens — an unauthenticated **welcome page** (top bar with Login, hero with marketing copy, an ISSF-target SVG) and an authenticated **app shell** (top bar with the user's nick, a collapsible left menu with Home + Logout-at-bottom, a dashboard placeholder). It also closes two deferred F-01 commitments: the **username-on-first-login** UX (FR-002) and **POST + CSRF logout**.

Alongside the frontend, S-01 introduces a **versioned, `bff`-prefix-free API surface** (`/v1/login`, `/v1/callback`, `/v1/logout`, `/v1/me`, `/v1/users`) — the module is still `src/bff/` (per AGENTS.md §4 directory structure), but the URL tree drops the `/bff/` and `/api/` prefixes in favour of a single `/v1/` version root. The new `set_nick` identity service + `PATCH /v1/me` land here, behind the existing `SessionAuth` + a new `has_set_nick` flag.

Roadmap slice: **S-01** (`context/foundation/roadmap.md` S-01 row). PRD refs: US-01, FR-001, FR-002, FR-012 (shell). Prerequisite F-01 (`oauth-roles-scaffold`) is done.

## Current State Analysis

The backend is **already SPA-ready** (F-01, archived done 2026-07-25). The frontend is **entirely absent**.

### Backend — what exists and works

- **OIDC redirect chain** — `src/bff/routers/auth_routes.py:59-117` ships `login_view` / `callback` / `logout`. `callback` (`:71`) does the Authlib code-exchange, resolves/creates the `User` by `sub`, calls Django's `login()`, redirects to `/`.
- **`GET /api/me`** — `src/bff/routers/session_routes.py:28-37`, `auth=session_auth`, `response={200: MeOut}`, returns `{authenticated, user:{nick, role}}`; 401 when unauthed. This is the SPA's auth seam.
- **`identity.User`** — `src/domains/identity/models.py:87-147`: UUID PK, unique `sub`, CI-unique `nick` (`CharField(max_length=64)`), derived `role` (reads `OWNER_SUB_ID` from env). `UserManager.create_user` (`:59-60`) defaults empty nicks to `_generated_nick()` → `shooter-<uuid8>`.
- **URL wiring** — `src/bff/urls.py:19-34`: `app_name = "bff"`, OIDC routes at `path("bff/login"...)` etc., `api.add_router("/")` (×2) mounts session + owner routers under `path("api/", api.urls)`, so today's surface is `/bff/login`, `/bff/callback`, `/bff/logout`, `/api/me`, `/api/users`. `reverse("bff:callback")` is used inside `callback` (`auth_routes.py:67`); `reverse("bff:index")` inside `logout` (`:112`). **These names reverse to today's `/bff/...` paths and are registered verbatim in the Auth0 dashboard's Allowed Callback / Logout URLs.**
- **Cookie/session** — `src/target_o_meter/settings.py:138-154`: `CSRF_COOKIE_HTTPONLY = False` (SPA reads `csrftoken`), `SESSION_COOKIE_SAMESITE = "Lax"` (load-bearing for the OIDC redirect — do not tighten), WhiteNoise at `:91`.
- **Dev bypass** — `src/target_o_meter/dev_auth_bypass.py`: stateless middleware auto-authenticates as `DEV_AUTH_BYPASS_SUB` when `DEBUG=True`; two-layer prod guard (`E001` + middleware self-gate). Day-to-day local SPA dev uses this, not Auth0.
- **Tests** — `tests/system/test_auth_flow.py:24-27` `_login_as(client, user)` helper using `client.force_login(..., backend="django.contrib.auth.backends.ModelBackend")`; every system test sets `pytestmark = [pytest.mark.django_db, pytest.mark.dev]`.

### Frontend — what's absent

- **`src/frontend/` is empty** — no `package.json`, no `index.html`, no entry point, no React/Redux/Oval/Vite (Node-side), no CSS, no TS config, no SVGs, no `static/`/`public/` dir.
- **`django-vite>=3.1.0`** is in the `dev` group (`pyproject.toml:39`) but **not imported or configured** — no `DJANGO_VITE_CFG`, no `VITE_APP_DIR`, `'django_vite'` not in `INSTALLED_APPS`, `STATICFILES_DIRS` unset.
- **`templates/base.html`** is a bare skeleton (`<html><head><body><main>`); no Vite tags, no `<div id="root">`.
- **`bff/views.py:14-18`** `index` dispatches on `request.user.is_authenticated` → renders `welcome.html` / `main.html`. S-01 changes this to serve the SPA bundle.

### Key Discoveries

- **The `sub` is the only identity anchor.** F-01's `sub`-keyed model means "first login" is purely a question of whether the user has chosen a nick yet — there's no separate "registered but not onboarded" state machine.
- **The nick column exists but has no "set" signal.** `UserManager.create_user` always assigns a `shooter-<uuid8>` nick, so emptiness/string-matching can't reliably detect first login. Decision (locked): add an explicit `has_set_nick BooleanField(default=False)` + one migration.
- **Renaming the OIDC redirect URLs requires an Auth0 dashboard change.** `reverse("bff:callback")` and the logout `returnTo` (`reverse("bff:index")`) feed URLs registered in Auth0's Allowed Callback / Allowed Logout URLs. The user does not yet have an Auth0 account — the real-Authorization smoke test is the **last phase** of this plan, and the dashboard URLs are set then.
- **`tests/system/test_templates.py:21-89` will break** on the React swap (it asserts substrings in server-rendered HTML). It is retired in Phase 3 and replaced with Vitest component tests + a pytest system test that asserts `/` serves the SPA shell.
- **The role is environmental, not persisted** (`OWNER_SUB_ID`). The app shell can already conditionally render an "Admin" entry for `role === "owner"` — parked for S-04 but the seam exists in `/v1/me`.
- **Mock/skeleton-first is in-house style.** F-02 shipped `MockDetector`; F-01 shipped an empty `GET /api/users` (`200 []`). S-01 shipping a dashboard **placeholder** (no hero stats, no wizard, no chart) is consistent — dashboard content is S-02/S-03.

## Desired End State

After this plan, a developer can:

1. Run `uv run python src/manage.py runserver` + `npm run dev` (or the Vite-built bundle served by Django in prod) and see the SPA at `/`.
2. With `DEV_AUTH_BYPASS_SUB` set (and `DEBUG=True`), land directly in the **app shell**: top bar shows the app title (left) and the dev user's nick (right); a collapsible left menu shows Home at the top and Logout pinned at the bottom; the main area shows a dashboard placeholder.
3. As a first-login user (`has_set_nick=False`), be prompted inline to set a nick; submitting it persists via `PATCH /v1/me` and the top bar updates to the new nick.
4. Click **Login** on the **welcome page** (unauthenticated state, `DEV_AUTH_BYPASS_SUB` unset) → full-page nav to `/v1/login` → Auth0 hosted UI → `/v1/callback` → back to `/` in the app shell.
5. Click **Logout** (bottom of the menu) → `POST /v1/logout` (CSRF-enforced) → session cleared → redirect to Auth0 `/v2/logout` → back to the welcome page.
6. Run `uv run pytest` (backend, all green incl. new `set_nick` + renamed-route tests) and `npm run test` (Vitest component tests) and `npm run build` (production bundle) and `uv run lint-imports` (architecture contracts still green).
7. Confirm the real Auth0 flow works end-to-end once (Phase 4 manual gate).

## What We're NOT Doing

- **Dashboard content** (hero stats, add-photo button, capture/upload wizard, results list, month chart) — deferred to **S-02** (`photo-detection-review`) and **S-03** (`accept-persist-dashboard`). The authenticated shell's main area is a placeholder.
- **Redux / Oval** — AGENTS.md names "React + Oval + Redux", but S-01 has no stateful flows (the nick + role fit in React state/context). Redux/Oval land in S-02 when the capture wizard introduces real client state.
- **Client-side router (React Router)** — S-01 has two screens selected by a single `GET /v1/me` call. React Router is introduced when S-02/S-03 add screens.
- **Owner admin UI** (FR-003/004/005) — that's slice **S-04**. The shell may conditionally render a disabled "Admin" entry for owners, but no admin functionality ships here.
- **Soft-delete, nick uniqueness enforcement beyond F-01's CI constraint, additional OAuth providers, gallery upload, manual hole correction** — all parked per the roadmap.
- **A user/password form** — never. F-01's dev-bypass (`DEV_AUTH_BYPASS_SUB`) covers local dev; real login uses Auth0's hosted UI. No credential form is built in any slice.
- **CI/CD changes for the frontend** — `npm run build` + `npm run test` are wired locally; wiring them into GitHub Actions is a separate concern (the roadmap explicitly parked CI/CD).

## Implementation Approach

Four phases, in strict order. Phase 1 is backend-only and fully testable without a frontend; Phase 2 introduces the toolchain with a trivial render; Phase 3 builds the real screens on top of the wired toolchain; Phase 4 is the manual real-Auth0 smoke test that the user deferred to the end.

The phase ordering exists so that **the URL rename + `set_nick` service + POST logout land and are verified via pytest before any frontend work** — the frontend then consumes the final `/v1/...` contract, not a moving target. Renaming after the frontend exists would mean rewriting every `fetch` call.

**House style:** backend follows the DDD + BFF conventions F-01/F-02 established (router in `src/bff/routers/` → service in `src/domains/identity/services.py` → ORM in `src/domains/identity/models.py`; Pydantic DTOs in `dtos.py`; one class per file per `lessons.md:12-17`; import-linter contracts 1 & 2 must stay green). Frontend follows React + TypeScript + CSS Modules conventions introduced in Phase 2.

**Architectural enforcement** (must stay green at every phase boundary):
- `.importlinter` contract:1 (domain independence) — the new `set_nick` service must not import from `vision` or `core`.
- `.importlinter` contract:2 (BFF above domains) — the new route lives in `src/bff/routers/session_routes.py` and calls the service; the router never touches the ORM.
- `ruff check .` clean; `pytest` green.

## Critical Implementation Details

- **Renaming routes is not purely code-local.** `src/bff/routers/auth_routes.py:67` (`reverse("bff:callback")`) and `:112` (`reverse("bff:index")`) produce URLs registered in the Auth0 dashboard. The `app_name = "bff"` (`src/bff/urls.py:19`) and the `name="callback"` / `name="index"` can stay (they're internal Django URL names, not path segments) — what changes is the **path prefix** (`"bff/login"` → `"v1/login"`) and the `api/` mount (`path("api/", api.urls)` → `path("v1/", api.urls)`). Auth0's Allowed Callback / Allowed Logout URLs are updated in Phase 4.
- **`PATCH /v1/me` must be CSRF-protected.** django-ninja auto-enforces CSRF for non-GET methods under `SessionAuth`; the SPA reads the `csrftoken` cookie (`CSRF_COOKIE_HTTPONLY = False`) and sends `X-CSRFToken`. No new middleware needed — verify with a test.
- **`SameSite=Lax` is load-bearing.** Do not "harden" the session cookie to `Strict` — it breaks the Auth0 callback (the cross-site redirect arrives without `sessionid`, Authlib finds no nonce, login fails silently). `settings.py:146` stays as-is.
- **`has_set_nick` default — schema-only, no backfill.** The new `BooleanField(default=False)` migration is schema-only with `default=False` and runs **no `RunPython` backfill**. The originally-considered backfill (`True` for non-`shooter-*` nicks) was dropped because (a) `create_superuser` produces a `shooter-*` nick via `_generated_nick()`, so the predicate would not spare the dev-admin anyway, and (b) there is no seeding migration, so the backfill is a no-op on every real DB. New OAuth users default to `False` and prompt on first login; a `createsuperuser`-created dev-admin will be prompted once (set `has_set_nick=True` via `shell` to skip).
- **django-vite dev vs prod paths.** In dev (`DEBUG=True`), django-vite serves from the Vite dev server (HMR); in prod, from the built manifest in `STATICFILES_DIRS`. The `STATICFILES_DIRS` entry pointing at `src/frontend/dist` is **only needed for prod** (`collectstatic`), but adding it unconditionally is harmless and avoids a dev/prod settings split for S-01's scope.
- **SPA catch-all ordering.** `/v1/*` and `/admin/` must win over the SPA's `/`. The index view at `""` is the SPA mount; Django's URLconf matches in order, so the versioned routes stay **above** the catch-all (they already do — `urls.py:25-33`). No SPA client-side router means no need for a `path("", ...)` catch-all beyond the index view.

## Phase 1: Backend foundation — versioned URLs, `set_nick`, POST logout

### Overview

All backend changes land first: rename the URL surface to `/v1/...`, add the `has_set_nick` migration, the `set_nick` service, `PATCH /v1/me`, and rework logout to POST + CSRF. Fully verifiable via pytest using the existing `force_login` pattern — no frontend needed.

### Changes Required:

#### 1.1 `has_set_nick` field + migration

**File**: `src/domains/identity/models.py`, new `src/domains/identity/migrations/0002_has_set_nick.py`

**Intent**: Add an unambiguous "has the user chosen a nick" signal so the SPA can decide whether to show the first-login prompt, replacing the fragile `shooter-*` string-match.

**Contract**: New `has_set_nick = models.BooleanField(default=False)` on `User` (after `nick`). Migration `0002` is **schema-only** — it adds the column with `default=False` and runs **no backfill**. Rationale: `UserManager.create_superuser` → `create_user` → `_generated_nick()` (`models.py:24-30, 56-64, 66-84`) produces a `shooter-<uuid8>` nick when no nick is passed, so the obvious "backfill `True` for non-`shooter-*` nicks" predicate would leave the dev-admin at `False` anyway — contradicting the intent. And there is no `RunPython` seeding users anywhere in the migration tree (`0001_initial` is schema-only), so against any real DB the backfill would be a no-op: the app isn't deployed and Phase 4 is the first real OAuth run. New OAuth users are `False` by default and prompt on first login (the desired UX). A dev-admin created via `createsuperuser` will be prompted once on first SPA visit — either accept that or set `has_set_nick=True` via `manage.py shell`. `UserManager.create_user` sets `has_set_nick=False` for new rows. The derived `role`/`is_owner` properties are unchanged.

#### 1.2 `set_nick` identity service

**File**: `src/domains/identity/services.py`

**Intent**: Provide the pure business logic the BFF route calls to set a nick — validate, enforce the existing CI-uniqueness constraint, set `has_set_nick=True`, return a DTO. One function, one file (per `lessons.md:12-17`).

**Contract**: `def set_nick(sub: str, nick: str) -> UserContextDTO:` — looks up the `User` by `sub` (raises `User.DoesNotExist` → BFF maps to 401), trims + length-validates `nick` (1–64 chars), catches `IntegrityError` on the `identity_user_nick_ci_unique` constraint and re-raises as a domain `NickTakenError` (or returns a sentinel — match the existing service style in `services.py:27-65`), otherwise sets `user.nick` + `user.has_set_nick = True`, saves, returns `get_user_context(sub)`. Add `UserContextDTO.has_set_nick: bool` to `dtos.py:18-31` so the route can expose it. **Also update `_user_to_context_dto()`** (`services.py:16-24`) — it is the sole construction site for `UserContextDTO`, so adding a non-optional field breaks it; populate the new field from the ORM (`has_set_nick=user.has_set_nick`). Pydantic raises at runtime if this is missed.

#### 1.3 `PATCH /v1/me` route

**File**: `src/bff/routers/session_routes.py`

**Intent**: The SPA's mutation endpoint for the first-login nick prompt. CSRF-enforced automatically by `SessionAuth` on non-GET.

**Contract**: `@router.patch("/me", auth=session_auth, response={200: MeOut, 409: ErrorOut})` — body `{nick: str}`, calls `set_nick(str(request.user.sub), payload.nick)`. Map `User.DoesNotExist → HttpError(401)` (existing pattern, `session_routes.py:31-33`), `NickTakenError → HttpError(409, "Nick already taken")`. Extend `MeOut` (`dtos.py:40-49`) so the 200 body includes `has_set_nick` (the SPA gates the prompt on it). Add a small `ErrorOut` DTO `{detail: str}` for the 409 if one doesn't exist (ninja provides a default detail schema — reuse if possible).

#### 1.4 URL rename: `/bff/*` and `/api/*` → `/v1/*`

**File**: `src/bff/urls.py`

**Intent**: Drop the `bff/` and `api/` path prefixes in favour of a single `/v1/` version root. The module is still `src/bff/` (AGENTS.md §4 directory structure); only the URL tree changes.

**Contract**: Keep `app_name = "bff"` (internal Django URL names are not path segments — `reverse("bff:callback")` still works). Change `path("bff/login", ...)` → `path("v1/login", ...)`, `"bff/callback"` → `"v1/callback"`, `"bff/logout"` → `"v1/logout"`. Change `path("api/", api.urls)` → `path("v1/", api.urls)` so the django-ninja routes (session + owner routers) resolve under `/v1/me`, `/v1/me` (PATCH), `/v1/users`. The `name=` attributes (`"login"`, `"callback"`, `"logout"`, `"index"`) are unchanged. **No change to `auth_routes.py:67`/`:112`** — they call `reverse("bff:callback")` / `reverse("bff:index")`, which now produce `/v1/callback` / `/` automatically.

#### 1.5 Logout: GET → POST + CSRF

**File**: `src/bff/routers/auth_routes.py`

**Intent**: Close the GET-logout CSRF-soft vector F-01 flagged (plan-review F5). The SPA POSTs to `/v1/logout` with the `X-CSRFToken` header.

**Contract**: Change `def logout(request)` from a GET view to a POST handler by decorating the existing plain Django view with `@require_POST` + `@csrf_protect` (from `django.views.decorators.http` and `django.views.decorators.csrf`). **Keep it as a plain Django view** — do not move it onto a ninja `Router`: `logout` is a 302-returning view in the same shape as `login_view` (`auth_routes.py:59`) and `callback` (`auth_routes.py:71`), all registered top-level in `urls.py` (not via `api.add_router`). The `router = Router()` declared at `auth_routes.py:37` is dead code (never mounted) — leave it or remove it, but do not use it. (Moving only `logout` to a ninja Router while leaving `login_view`/`callback` as Django views would split the OIDC chain across two styles — less consistent, not more. ninja's auto-CSRF does not apply to plain Django views, hence `@csrf_protect`.) Keep the session-clear + Auth0 `/v2/logout` redirect logic (`auth_routes.py:103-117`). The `returnTo` URL is still `reverse("bff:index")` → `/`. Update the docstring (the "GET-based in F-01" comment is now stale).

#### 1.6 Index view serves the SPA shell

**File**: `src/bff/views.py`

**Intent**: `/` now serves the SPA bootstrap document regardless of auth state (the SPA decides welcome vs. shell client-side via `GET /v1/me`).

**Contract**: `index` renders a single template (the reworked `base.html`, Phase 2) carrying the django-vite tags + `<div id="root">`. Drop the `request.user.is_authenticated` dispatch (`views.py:16-18`) and the two-template split — both auth states are handled in the SPA. The template name can stay `base.html` or become `spa.html`; either is fine, keep `index` as the named URL (`reverse("bff:index")` is used by logout's `returnTo`).

#### 1.7 Backend tests for the rename + new endpoint + POST logout

**File**: `tests/system/test_auth_flow.py` (extend), `tests/system/test_dev_bypass.py` (extend — `/api/me` → `/v1/me` at lines 43 and 63), `src/domains/identity/tests/test_services.py` (extend)

**Intent**: Cover the renamed routes, the new `set_nick` service + `PATCH /v1/me`, the POST logout, and the `has_set_nick` backfill — using the existing `force_login` pattern, not Auth0.

**Contract**: Update `test_auth_flow.py` URLs from `/api/me`, `/api/users` to `/v1/me`, `/v1/users`. Add: `PATCH /v1/me` happy path (200, nick updated, `has_set_nick=True`), 409 on duplicate nick, 401 when unauthed. Add a test that `GET /v1/logout` is no longer accepted (405) and `POST /v1/logout` clears the session (use the Django test client's `post`). **Add explicit CSRF tests** — `PATCH /v1/me` returns 403 (or 401 if CSRF fires before auth — pin down which) without an `X-CSRFToken` header, 200 with a valid token sourced from the `csrftoken` cookie; same shape for `POST /v1/logout`. This is load-bearing: the codebase has no non-GET endpoint today, so ninja's auto-CSRF-for-SessionAuth claim (asserted only in `bff/api.py:3-6`) becomes a tested invariant here, not a docstring promise. In `test_services.py`, add `set_nick` tests (happy path, `NickTakenError` on CI-duplicate, trims whitespace, length validation). No migration-backfill test (0002 is schema-only — see §1.1). Retire `tests/system/test_templates.py` here too (it will assert on the old welcome/main templates) — replace its coverage with a single test asserting `/` returns 200 and contains `<div id="root">`.

### Success Criteria:

#### Automated Verification:

- `uv run python src/manage.py makemigrations identity` produces a clean `0002_has_set_nick` (or no-op if the field was added manually) — run, then verify the migration file exists and is wired.
- `uv run python src/manage.py migrate` applies cleanly on a fresh DB.
- `uv run pytest` is green, including new `set_nick` + `PATCH /v1/me` + POST logout + migration-backfill tests, and the renamed-route assertions in `test_auth_flow.py`.
- `uv run ruff check .` is clean.
- `uv run lint-imports` is green (contracts 1 & 2 — the new service doesn't cross domains; the new route stays in `bff`).
- `uv run python src/manage.py check` passes (E001/W001 guards intact).

#### Manual Verification:

- `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/v1/me` (no session) returns 401.
- `curl ... http://localhost:8000/bff/login` returns 404 (route renamed); `/v1/login` returns 302 (redirect to Auth0 — will 500/error without Auth0 creds, which is expected until Phase 4).
- With `DEV_AUTH_BYPASS_SUB` set, `curl -s http://localhost:8000/v1/me` returns 200 with the dev user's `nick` + `has_set_nick`.

**Implementation Note**: After completing this phase and all automated verification passes, pause for manual confirmation from the human that the manual testing was successful before proceeding to Phase 2.

---

## Phase 2: Frontend toolchain — Node, Vite, React, django-vite wired

### Overview

Introduce the Node/Vite/React/TS toolchain and wire django-vite into Django. This phase ends with a trivial "Hello from React" render at `/` served via django-vite (HMR in dev, built bundle in prod) — no real UI yet. The goal is to de-risk the Django↔Vite handoff before building screens.

### Changes Required:

#### 2.1 `src/frontend/` package + Vite config

**Files**: `src/frontend/package.json`, `src/frontend/vite.config.ts`, `src/frontend/tsconfig.json`, `src/frontend/index.html`, `src/frontend/.gitignore`, `src/frontend/src/main.tsx`

**Intent**: Stand up a standard Vite + React + TS project under `src/frontend/`, configured to build a bundle Django can serve.

**Contract**: `package.json` with `react`, `react-dom`, dev deps `vite`, `@vitejs/plugin-react`, `typescript`, `vitest`, `@testing-library/react`, `@testing-library/jest-dom`, `jsdom`. Scripts: `dev` (`vite`), `build` (`tsc && vite build`), `test` (`vitest run`), `test:watch` (`vitest`). `vite.config.ts` sets `plugins: [react()]`, `build.outDir: 'dist'`, `build.manifest: true` (django-vite needs the manifest), `test.environment: 'jsdom'`, `test.globals: true`, `test.setupFiles: ['./src/test-setup.ts']`. `index.html` references `/src/main.tsx` and has `<div id="root"></div>`. `tsconfig.json` standard Vite React-TS config. `src/frontend/.gitignore` ignores `node_modules/` and `dist/`.

#### 2.2 django-vite wired into Django settings + base template

**Files**: `src/target_o_meter/settings.py`, `templates/base.html`

**Intent**: Connect the Vite build/HMR to Django so `{% vite %}` tags in the template resolve correctly in both dev (HMR via Vite dev server) and prod (hashed bundle from manifest).

**Contract**: `settings.py` — add `'django_vite'` to `INSTALLED_APPS`; add `DJANGO_VITE_CFG = {"default": {"dev_mode": settings.DEBUG, "nested_entry": "src/main.tsx", "manifest_path": BASE_DIR / "frontend" / "dist" / "manifest.json", "build_dev_server": "http://localhost:5173"}}` (paths relative to the actual `src/frontend/` location — verify against the chosen `VITE_APP_DIR`). Add `STATICFILES_DIRS = [BASE_DIR / "frontend" / "dist"]` so `collectstatic` picks up the built bundle in prod. `templates/base.html` — add `{% load django_vite %}` at top, `{% vite_react_refresh %}` in `<head>`, `{% vite 'src/main.tsx' %}` before `</body>`, `<div id="root"></div>` in `<main>`, **and `{% csrf_token %}`** (the SPA's PATCH /v1/me and POST /v1/logout both read the `csrftoken` cookie; Django only sets that cookie when `get_token()` runs during the request — `{% csrf_token %}` guarantees it on every `/` render, including the unauthed first load, so the first PATCH/POST has a token to send). Keep `{% block %}` hooks minimal — this is now the SPA shell, not a content template.

#### 2.3 Trivial render to verify the pipeline

**File**: `src/frontend/src/main.tsx`

**Intent**: Prove the Django↔Vite handoff works before building real UI.

**Contract**: Minimal `ReactDOM.createRoot(document.getElementById('root')!).render(<h1>Hello from React</h1>)`. Replaced in Phase 3.

#### 2.4 Trivial Vitest test to verify the test runner

**File**: `src/frontend/src/main.test.tsx`

**Intent**: Confirm Vitest + Testing Library + jsdom are wired and runnable.

**Contract**: A single `it('renders hello', ...)` test using `@testing-library/render` against the trivial component. Replaced/expanded in Phase 3.

### Success Criteria:

#### Automated Verification:

- `cd src/frontend && npm install` completes; `node_modules/` + `package-lock.json` exist.
- `cd src/frontend && npm run build` produces `dist/` with `manifest.json` + hashed assets.
- `cd src/frontend && npm run test` passes the trivial test.
- `cd src/frontend && npx tsc --noEmit` is clean.
- `uv run python src/manage.py check` passes (django-vite configured without errors).

#### Manual Verification:

- `uv run python src/manage.py runserver` (Django :8000) + `cd src/frontend && npm run dev` (Vite :5173) → open `http://localhost:8000/` → see "Hello from React" rendered (with HMR: edit `main.tsx`, watch the browser update without a full reload).
- With `DEBUG=False` simulation (or just `npm run build` then runserver): `http://localhost:8000/` still renders from the built bundle (no Vite dev server needed).

**Implementation Note**: After completing this phase and all automated verification passes, pause for manual confirmation from the human that HMR and the prod build both render at `/` before proceeding to Phase 3.

---

## Phase 3: Welcome page, app shell, nick-on-first-login

### Overview

Build the two real screens on the now-wired toolchain: welcome page (unauthenticated) with hero + SVG, and the authenticated app shell with top bar + collapsible sidebar + dashboard placeholder. Wire the `GET /v1/me` auth seam, the conditional render, the nick-on-first-login prompt (`PATCH /v1/me`), and POST logout. Replace the trivial Phase 2 render. Add Vitest component tests; retire `tests/system/test_templates.py`.

### Changes Required:

#### 3.1 API client + types

**File**: `src/frontend/src/api.ts` (new)

**Intent**: Typed wrapper over the versioned `/v1/...` surface so components aren't littered with `fetch` calls and magic strings.

**Contract**: `type Me = { authenticated: boolean; user: { nick: string; role: 'owner'|'user'; has_set_nick: boolean } | null }`. `async function getMe(): Promise<Me>` (GET `/v1/me`, treat 401 as `{authenticated:false, user:null}`). `async function patchMe(nick: string): Promise<Me>` (PATCH `/v1/me` with JSON body, reads `csrftoken` cookie, sets `X-CSRFToken` header). `async function postLogout(): Promise<void>` (POST `/v1/logout`, same CSRF handling). `async function login(): never { window.location.href = '/v1/login' }` (full-page nav).

#### 3.2 ISSF-target SVG asset

**File**: `src/frontend/assets/target.svg` (new)

**Intent**: The welcome-page illustration — a generic concentric-ring ISSF pistol target (10 at centre → 1 outermost, black inner bull, small X ring), original artwork (no licensing/privacy concern).

**Contract**: Square `viewBox` (e.g. `0 0 200 200`), concentric `<circle>` rings, black inner zone (rings 7–10), small inner X ring, ring numbers as `<text>`, cream/off-white background. Imported into the welcome component via `import targetUrl from './assets/target.svg'` (Vite-hashed). Generic, not a real shooter's target.

#### 3.3 App component — auth seam + conditional render

**File**: `src/frontend/src/App.tsx` (new, replaces trivial `main.tsx` body)

**Intent**: The single decision point: fetch `/v1/me` on mount, render welcome (unauthed) or app shell (authed). Defer React Router — two screens, one `useState`.

**Contract**: `function App() { const [me, setMe] = useState<Me|null>(null); useEffect(() => { getMe().then(setMe) }, []) }`. Loading state → simple spinner. `me?.authenticated` falsy → `<Welcome onLogin={login} />`. Truthy → `<AppShell me={me!} onNickSet={...} onLogout={...} />`. When `me.user.has_set_nick` is false → render the nick prompt (inline or modal — see 3.5) before/over the shell.

#### 3.4 Welcome page component

**File**: `src/frontend/src/components/Welcome.tsx`, `Welcome.module.css`

**Intent**: The unauthenticated landing page — modern-app look.

**Contract**: Top bar (app title left, "Login" button right → `onLogin`). Hero section: marketing copy generated for ISSF sport shooters (concise, benefit-led — "Photograph your target. Get an objective score. Track your progress."), the ISSF-target SVG (3.2) as the hero image, a primary CTA button that also triggers `onLogin`. CSS Modules for layout. Component test: renders title, login button, hero copy; clicking login calls `onLogin`.

#### 3.5 App shell component — top bar + collapsible sidebar

**File**: `src/frontend/src/components/AppShell.tsx`, `AppShell.module.css`, `Sidebar.tsx`, `TopBar.tsx`

**Intent**: The authenticated layout — top bar (title left, nick right) + collapsible left menu (Home top, Logout bottom) + dashboard placeholder in the main area.

**Contract**: `TopBar` shows app title (left) + `me.user.nick` (right). `Sidebar` has a collapse/expand toggle (state in `AppShell`), a "Home" entry at the top (returns to the dashboard placeholder — no-op in S-01 since there's only one screen), and a "Logout" entry **pinned to the bottom** (flexbox `margin-top: auto` on the logout item). Main area shows a dashboard placeholder ("Your dashboard will appear here" + note that scoring lands in a later update). Optionally render a disabled "Admin" entry when `me.user.role === 'owner'` (visual seam for S-04). Logout calls `onLogout` → `postLogout()` then `window.location.reload()` (back to welcome). Component tests: top bar renders nick; sidebar collapses/expands; logout button is at the bottom; Home is at the top.

#### 3.6 Nick-on-first-login prompt

**File**: `src/frontend/src/components/NickPrompt.tsx`, `NickPrompt.module.css`

**Intent**: The FR-002 UX — shown when `me.user.has_set_nick === false`. Modal or inline overlay over the app shell.

**Contract**: Text input for the nick (1–64 chars), submit button → `patchMe(nick)` → on success `onNickSet(updatedMe)` (App updates state, prompt disappears, top bar shows new nick). On 409 (nick taken) show inline error. Disable submit while pending. Prevent dismissal without setting a nick (first-login is mandatory). Component test: renders when `has_set_nick=false`; submit calls `patchMe`; 409 shows error; can't dismiss empty.

#### 3.7 CSS Modules + global styles

**Files**: `src/frontend/src/styles.css` (global resets, CSS variables for the app's colour palette), `*.module.css` per component

**Intent**: Layout-driven styling without a CSS framework. Plain CSS Modules keep the toolchain minimal and match S-01's "no stateful flows" scope.

**Contract**: Global `styles.css` with box-sizing reset, font stack, CSS custom properties (`--color-bg`, `--color-fg`, `--color-primary`, sidebar width vars for collapsed/expanded). Each component owns its `<Name>.module.css`. No Tailwind, no CSS-in-JS.

#### 3.8 Retire `test_templates.py`, add SPA-shell system test

**File**: `tests/system/test_templates.py` (delete or replace), `tests/system/test_spa_shell.py` (new)

**Intent**: The old test asserts substrings in `welcome.html`/`main.html` — both gone. Replace with a test that `/` serves the SPA shell document.

**Contract**: `test_spa_shell.py` — `GET /` returns 200, body contains `<div id="root">` and the django-vite script tag; unauthenticated and authenticated both get the same shell (the dispatch moved client-side). Use the existing `client` + `force_login` pattern.

### Success Criteria:

#### Automated Verification:

- `cd src/frontend && npm run test` — all component tests green (Welcome, AppShell, Sidebar, TopBar, NickPrompt).
- `cd src/frontend && npx tsc --noEmit` clean.
- `cd src/frontend && npm run build` produces `dist/`.
- `uv run pytest` green including `test_spa_shell.py`; `tests/system/test_templates.py` removed.
- `uv run ruff check .` + `uv run lint-imports` green.

#### Manual Verification:

- With `DEV_AUTH_BYPASS_SUB` set to a value ≠ `OWNER_SUB_ID` (plain user): `/` lands in the app shell, top bar shows `dev-<sub>` nick, sidebar collapses/expands, Home at top, Logout at bottom, dashboard placeholder visible. If the dev user's nick is `shooter-*` (i.e. `has_set_nick=false`), the nick prompt appears and setting a nick updates the top bar.
- Set `DEV_AUTH_BYPASS_SUB == OWNER_SUB_ID`: same shell, optionally shows the disabled "Admin" entry.
- Unset `DEV_AUTH_BYPASS_SUB` (no Auth0 creds yet): `/` shows the welcome page with hero + SVG + Login button.
- Click Logout (with bypass on): session clears, page reloads to welcome (POST logout works even without Auth0 — the Auth0 `/v2/logout` redirect may error without creds, which is fine; the Django session is already cleared).

**Implementation Note**: After completing this phase and all automated verification passes, pause for manual confirmation from the human that the welcome page, app shell, nick prompt, and logout all behave correctly before proceeding to Phase 4.

---

## Phase 4: Real Auth0 smoke test (manual gate)

### Overview

The user deferred real-Auth0 verification to the end. This phase has no code changes — it's a manual checklist: create the Auth0 account, configure the `/v1/...` URLs in the dashboard, and run the full login → callback → set-nick → logout flow against real Auth0.

### Changes Required:

#### 4.1 Create the Auth0 application

**Intent**: Stand up the Auth0 tenant + application the BFF expects.

**Contract**: Create an Auth0 tenant, a Regular Web Application, note `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, `AUTH0_DOMAIN`. Set the application's **Allowed Callback URLs** to `http://localhost:8000/v1/callback` (dev) — add the prod URL when deploying. Set **Allowed Logout URLs** to `http://localhost:8000/` (the logout `returnTo` resolves to `reverse("bff:index")` → `/`). Set **Allowed Web Origins** to `http://localhost:8000`.

#### 4.2 Populate `.env` and run the real flow

**Intent**: Verify the full OAuth round-trip against the renamed `/v1/...` surface.

**Contract**: In `.env`, set `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, `AUTH0_DOMAIN`, `OWNER_SUB_ID` (to your real Auth0 `sub`), and **unset `DEV_AUTH_BYPASS_SUB`**. Run the dev server. Manual steps: open `/` → welcome page → click Login → Auth0 hosted UI → log in → redirected to `/v1/callback` → back at `/` in the app shell with your nick shown → first-login nick prompt (if `has_set_nick=false`) → set nick → click Logout → POST `/v1/logout` → Auth0 `/v2/logout` → back at welcome.

### Success Criteria:

#### Automated Verification:

- None — this phase is a manual external-dependency check. The automated guards (pytest, Vitest, build) remain green from Phase 3.

#### Manual Verification:

- Real Auth0 login completes: `/v1/login` → Auth0 → `/v1/callback` → app shell at `/`.
- The logged-in user's real `sub` appears in `/v1/me` (verify via browser devtools network tab or `curl` with the session cookie).
- First-login nick prompt works for a brand-new Auth0 user (the row is created with `has_set_nick=false`).
- Logout completes the full round-trip back to the welcome page.
- Re-login (returning user, `has_set_nick=true`) skips the nick prompt.

**Implementation Note**: This is the final gate. If anything fails here, the issue is almost certainly the Auth0 dashboard URLs (Allowed Callback / Logout URLs matching `/v1/callback` and `/`) or a mismatch between `OWNER_SUB_ID` and the real `sub`. Fix in the Auth0 dashboard or `.env`, not in code — the code is verified by Phases 1–3.

---

## Testing Strategy

### Unit Tests:

- **`set_nick` identity service** (`src/domains/identity/tests/test_services.py`) — happy path, CI-uniqueness violation, whitespace trim, length bounds, sets `has_set_nick=True`. Uses `make_user`/`make_owner` from `test_utils.py` (no ORM factories).
- **Migration `0002`** is schema-only (no `RunPython`); no backfill test applies.

### Integration / System Tests:

- **`PATCH /v1/me`** (`tests/system/test_auth_flow.py`) — 200 happy path, 409 on duplicate nick, 401 unauthed, 403/401 without `X-CSRFToken` (locks the ninja-auto-CSRF invariant — no non-GET endpoint exists yet to prove it).
- **Renamed routes** — `/v1/me`, `/v1/users`, `/v1/login` (302), `/v1/logout` (POST only, GET → 405; POST 403/401 without CSRF).
- **POST logout** — clears the session.
- **SPA shell** (`tests/system/test_spa_shell.py`, new) — `/` returns 200 with `<div id="root">`, same response authed and unauthed.
- **`tests/system/test_templates.py`** — retired (broken by the React swap).

### Frontend Tests (Vitest + Testing Library):

- `Welcome` renders title, login button, hero copy, SVG; click login → `onLogin`.
- `AppShell` renders top bar (title + nick), sidebar collapses/expands, Home at top, Logout at bottom.
- `NickPrompt` renders on `has_set_nick=false`, submit → `patchMe`, 409 → error, empty submit disabled.
- `api.ts` (light — mostly type + fetch wiring; one test that `getMe` maps 401 to `{authenticated:false}`).

### Deferred (UAT — later slice):

- Full Playwright UAT against real Auth0 is already scaffolded (`tests/acceptance/conftest.py:24-48`, skip-on-missing-secret). No UAT test lands in S-01 — the manual Phase 4 gate covers the real-Auth0 check once.

### Manual Testing Steps:

1. **Phase 1**: `curl /v1/me` → 401; with bypass → 200 + `has_set_nick`. `curl /bff/login` → 404; `/v1/login` → 302.
2. **Phase 2**: `runserver` + `npm run dev` → "Hello from React" at `/` with HMR; `npm run build` → same at `/` without the dev server.
3. **Phase 3**: Bypass-on → app shell, nick prompt, logout; bypass-off → welcome page. Toggle `DEV_AUTH_BYPASS_SUB == OWNER_SUB_ID` → owner seam.
4. **Phase 4**: Real Auth0 round-trip per §4.2.

## Performance Considerations

- The SPA is tiny (two screens, no chart, no images beyond one SVG). No performance budget concerns in S-01.
- django-vite serves the dev server (HMR) in dev and the hashed bundle (WhiteNoise) in prod — standard, no special handling.
- Vite's manifest + content-hashed filenames give long-cache-friendly assets in prod automatically.
- No SSR, no hydration — a plain client-rendered SPA. `GET /v1/me` on mount is the only network waterfall; acceptable for S-01 (and unavoidable given the BFF/session architecture).

## Migration Notes

- **DB migration `0002_has_set_nick`** is the only schema change — schema-only, adds the column with `default=False`, no `RunPython` backfill (see Critical Implementation Details for the rationale). New OAuth users → `False` (prompt on first login). Rollback: `migrate identity 0001` drops the column; the `nick` data is untouched. No data loss risk.
- **URL rename is a breaking change to the URL surface** but has no DB impact. The only external dependency is the Auth0 dashboard (Phase 4). Existing F-01 tests are updated in Phase 1 to the new paths — `test_auth_flow.py`, `test_dev_bypass.py`, and `test_templates.py` (the last retired, not edited). Before merging, grep the repo (excluding `.venv`/`node_modules`/`context/`) for `"/api/` and `"/bff/` to confirm no other hardcoded literal slipped through; the only `reverse(...)` calls (`bff:callback`, `bff:index` in `auth_routes.py`) keep working because the URL *names* are unchanged — only the path prefixes move.
- **No production data exists yet** (the app isn't deployed), so the migration runs against empty/dev DBs only. No coordinated downtime needed.

## References

- Research: `context/changes/sign-in-empty-dashboard/research.md` (codebase baseline, §10 auth-modes clarification)
- F-01 plan (house-style template for this plan): `context/archive/2026-07-24-oauth-roles-scaffold/plan.md`
- F-01 research (OAuth/identity/session detail): `context/archive/2026-07-24-oauth-roles-scaffold/research.md`
- Roadmap S-01 row: `context/foundation/roadmap.md:34`
- Lessons: `context/foundation/lessons.md:12-17` (one class per file)
- AGENTS.md §4 (directory structure — `src/bff/` stays the module name), §5 (boundary rules), §6 (BFF atomicity example)
- django-vite docs: https://github.com/NikolaLohinski/django-vite (template tags, manifest config)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Backend foundation — versioned URLs, `set_nick`, POST logout

#### Automated

- [x] 1.1 `has_set_nick` field on `User` + schema-only migration `0002` (no backfill)
- [x] 1.2 `set_nick` service in `src/domains/identity/services.py` (+ `NickTakenError`); update `_user_to_context_dto` to populate `has_set_nick`
- [x] 1.3 `PATCH /v1/me` route + `MeOut.has_set_nick` extension
- [x] 1.4 URL rename `/bff/*` + `/api/*` → `/v1/*` in `src/bff/urls.py`
- [x] 1.5 Logout GET → POST + CSRF (`@require_POST` + `@csrf_protect`, kept as plain Django view) in `src/bff/routers/auth_routes.py`
- [x] 1.6 `index` view serves the SPA shell document
- [x] 1.7 Backend tests updated (rename incl. `test_dev_bypass.py` + `set_nick` + POST logout + explicit CSRF tests); `test_templates.py` retired
- [x] 1.8 `uv run python src/manage.py makemigrations identity` → verify `0002_has_set_nick` file shape; `uv run python src/manage.py migrate` applies cleanly on a fresh DB
- [x] 1.9 `uv run pytest` green
- [x] 1.9 `uv run ruff check .` + `uv run lint-imports` + `uv run python src/manage.py check` green

#### Manual

- [x] 1.10 `curl /v1/me` → 401; with bypass → 200 + `has_set_nick`; `/bff/login` → 404, `/v1/login` → 302

### Phase 2: Frontend toolchain — Node, Vite, React, django-vite wired

#### Automated

- [x] 2.1 `src/frontend/` package + Vite config (`package.json`, `vite.config.ts`, `tsconfig.json`, `index.html`, `main.tsx`) — 127ff39
- [x] 2.2 django-vite wired in `settings.py` (`INSTALLED_APPS`, `DJANGO_VITE`, `STATICFILES_DIRS`) + `templates/base.html` (Vite tags + `<div id="root">`) — 127ff39
- [x] 2.3 Trivial render in `main.tsx` + trivial Vitest test — 127ff39
- [x] 2.4 `npm install` + `npm run build` + `npm run test` + `tsc --noEmit` clean; `manage.py check` clean — 127ff39

#### Manual

- [x] 2.5 `runserver` + `npm run dev` → "Hello from React" at `/` with HMR; prod build renders without dev server — 127ff39

### Phase 3: Welcome page, app shell, nick-on-first-login

#### Automated

- [x] 3.1 API client `src/frontend/src/api.ts` (typed `getMe`/`patchMe`/`postLogout`/`login` + CSRF) — cef7d23
- [x] 3.2 ISSF-target SVG at `src/frontend/assets/target.svg` — cef7d23
- [x] 3.3 `App.tsx` auth seam + conditional render — cef7d23
- [x] 3.4 `Welcome` component (+ CSS Module) + test — cef7d23
- [x] 3.5 `AppShell` + `Sidebar` + `TopBar` (+ CSS Modules) + tests (collapse, Home-top, Logout-bottom, nick) — cef7d23
- [x] 3.6 `NickPrompt` component (+ CSS Module) + test (409, empty-disabled, success) — cef7d23
- [x] 3.7 Global `styles.css` + per-component CSS Modules — cef7d23
- [x] 3.8 `tests/system/test_spa_shell.py` (new) + retire `tests/system/test_templates.py` — cef7d23
- [x] 3.9 `npm run test` + `tsc --noEmit` + `npm run build` clean; `uv run pytest` + `ruff` + `lint-imports` green — cef7d23

#### Manual

- [x] 3.10 Bypass-on (plain user) → app shell + nick prompt + logout; bypass-on (==OWNER_SUB_ID) → owner seam; bypass-off → welcome page — cef7d23

### Phase 4: Real Auth0 smoke test (manual gate)

> **Reserved as the owner's out-of-session gate (2026-07-25).** Phases 1–3 are
> committed and system-tested; Phase 4 is a manual external-credentials round-trip
> (no code) that the owner will run with their own Auth0 tenant. The two rows
> below stay `[ ]` deliberately — they are NOT pending implementation, they are
> pending the owner's real Auth0 dashboard + `.env` setup. The code verified by
> Phases 1–3 is what Phase 4 exercises end-to-end.

#### Manual

- [x] 4.1 Create Auth0 tenant + app; set Allowed Callback (`/v1/callback`), Allowed Logout (`/`), Web Origins in dashboard  *(owner's out-of-session gate)* — ae89a16
- [x] 4.2 Populate `.env` (`AUTH0_*`, `OWNER_SUB_ID`, unset `DEV_AUTH_BYPASS_SUB`) and run the real login → callback → set-nick → logout round-trip  *(owner's out-of-session gate)* — ae89a16

### Phase 5: Auth0 integration hardening — dotenv, static pipeline, route prefix, owner bootstrap

> Added 2026-07-26 after the first `DEBUG=false make dev` smoke surfaced four real
> integration gaps the system tests didn't catch: (a) `oauth.auth0.authorize
> _redirect` crashed on `Invalid URL 'https:///.well-known/...'` because nothing on
> the Django path calls `load_dotenv()`; (b) the SVG/SPA vanished because the prod
> static pipeline (`collectstatic` + WhiteNoise storage) was never wired; (c) the
> OIDC routes carry a `/v1` prefix the owner wants gone; (d) `OWNER_SUB_ID` is
> unknowable before the first login because Auth0's `sub` is opaque. Phase 5 lands
> red→green TDD fixes for each. Phase 4 (the real-Auth0 manual gate) stays `[ ]`
> but becomes exercisable once 5.x lands.

#### Automated

- [x] 5.1 RED: backend tests in `tests/system/test_auth_flow.py` + `test_spa_auth_seam.py` hit `/login`, `/callback`, `/logout` (not `/v1/*`); add a `/v1/login` → 404 regression guard — ae89a16
- [x] 5.2 GREEN: rename `path("v1/login"|"v1/callback"|"v1/logout", ...)` → `path("login"|"callback"|"logout", ...)` in `src/bff/urls.py` (NinjaAPI mount at `path("v1/", api.urls)` untouched; `app_name` + `name=` attrs unchanged so `reverse()` keeps resolving) — ae89a16
- [x] 5.3 RED+GREEN: `src/frontend/src/api.test.ts` + `api.ts` — `login()` navigates to `/login` (literal `'/v1/login'` → `'/login'`) — ae89a16
- [x] 5.4 RED: `tests/system/test_env_loading.py` (new) — `.env`-loading guard (bespoke `AUTH0_DOMAIN` round-trips through `load_dotenv` into a 302) + `.env.example` drift guard (every `KEY=` read somewhere under `src/`) — ae89a16
- [x] 5.5 GREEN: `load_dotenv()` at top of `src/target_o_meter/settings.py`; wire orphaned env vars (`AUTH0_SECRET`→`SECRET_KEY`, `APP_BASE_URL`→`ALLOWED_HOSTS`); update `.env.example` — ae89a16
- [x] 5.6 RED: extend `tests/system/test_spa_pipeline.py:test_index_serves_spa_shell_prod_mode` to fetch the hashed bundle URL + assert 200 + JS content-type + SVG-inlined `data:image/svg+xml;base64,` in bundle body — ae89a16
- [x] 5.7 GREEN: add `STORAGES` (WhiteNoise `CompressedManifestStaticFilesStorage`) in `settings.py`; run `collectstatic` in `tests/system/conftest.py:_boot_runserver`; add `make collectstatic` + `make prod` targets — ae89a16
- [x] 5.8 RED+GREEN: callback logs the first-ever login's `sub` extra-loud (`logger.warning`) and subsequent logins `info`; new test under `tests/system/` mocking `oauth.auth0.authorize_access_token` + asserting the captured logs — ae89a16
- [x] 5.9 `make check` (ruff + import-linter + tsc) + `make be-test` + `make fe-test` green — ae89a16
- [x] 5.11 RED+GREEN: dev-mode Vite proxy — Vite emits bare `/static/...` paths for asset imports (even with `base` as a full origin URL — Vite's module-graph rewriting strips it); browsers resolve these against the document origin (`:8000`) not the importing module's origin (`:5173`), so Django's `StaticFilesHandler` 404s them and the welcome-page SVG vanishes in `make dev`. Fix: custom `runserver` command (`src/target_o_meter/management/commands/runserver.py`) wraps the WSGI app in `ViteProxyStaticFilesHandler` (`src/bff/dev_vite_proxy.py`) that proxies staticfiles misses to Vite. New test `test_dev_mode_proxy_serves_vite_assets_at_django_origin` pins the contract; gated on `DEBUG=True` so prod (WhiteNoise) is unaffected — ae89a16

#### Manual

- [x] 5.10 `DEBUG=false make prod` → `/` renders the SPA (SVG visible); click Login → no `InvalidURL` crash; callback logs `sub`; set `OWNER_SUB_ID=<logged sub>` + restart → `/v1/me` returns `role: "owner"` — ae89a16
- [x] 5.12 `make dev` (DEBUG=true) at `http://localhost:8000/` → welcome-page hero SVG renders (no 404 on `/static/assets/target.svg`); SPA modules load through Vite via the dev proxy — ae89a16
