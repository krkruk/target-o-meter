# OAuth + Roles Scaffold (F-01) — Plan Brief

> Full plan: `context/changes/oauth-roles-scaffold/plan.md`
> Research: `context/changes/oauth-roles-scaffold/research.md` (Q1–Q8 all resolved)

## What & Why

Wire the full auth vertical for Target-o-meter: Auth0 OIDC (Authorization Code + PKCE) sign-in via a Django BFF, a custom zero-email identity model whose Owner/User role is *derived* from an env var (never persisted), an RBAC skeleton proven by one demo owner route, a dev-only auth bypass with a three-layer production guard, Django templates for the welcome/login/main shell, DEV/UAT test scaffolding, and a Docker dev environment. This is F-01 from `roadmap.md` — the minimal enabler that unlocks S-01 (dashboard) and S-04 (real owner actions).

## Starting Point

The codebase at commit `eebc2f8` has the DDD skeleton (`src/domains/{core,identity,vision}/`) with `vision/` as the fully-implemented reference domain and `identity/` as docstring-only stubs. The entire `src/bff/` layer is a 0-byte `__init__.py`; `src/frontend/` is empty; `tests/{system,acceptance}/` are empty; no Docker/CI exists. `settings.py` has `SessionMiddleware` + `AuthenticationMiddleware` in place but no `AUTH_USER_MODEL`, no cookie hardening, and `django-ninja` is declared but unused. The `auth_user` table is empty, so the `AUTH_USER_MODEL` swap is clean-slate safe.

## Desired End State

A developer runs `docker compose -f docker-compose.dev.yml up` and gets a live-reloading Django + qcluster with a seeded DB (dev admin, Owner, User). Clicking Login redirects through Auth0 (social + magic-link; no passwords) back to a main page reading "logged in as {nick} ({role})". `GET /api/me` returns nick+role (no sub) for authed users, 401 otherwise. `GET /api/users` returns 401 (anon), 403 (User), or 200 `[]` (Owner) — proving the RBAC contract. Locally, `DEV_AUTH_BYPASS_SUB` skips Auth0 entirely, and if it's ever set with `DEBUG=False`, the app refuses to boot. `uv run pytest` runs the DEV suite; UAT is opt-in and its test is deferred.

## Key Decisions Made

| Decision | Choice | Why | Source |
|---|---|---|---|
| Auth flow / tokens | Auth0 OIDC Auth Code + PKCE; server-side Django session; `sessionid` HttpOnly cookie | BFF pattern mandated by AGENTS.md §2; PKCE is defense-in-depth | Research |
| IdP | Auth0 (not Google) | User mandate; federates Google/MS/GitHub + passwordless | Research |
| Login methods | Passwordless only (social + magic-link); Auth0 Database disabled | Minimizes data-leakage surface | Research |
| Identity model | `AbstractBaseUser`, fields `sub` + `nick` + `is_staff`; no `PermissionsMixin` | Minimal base that still plugs into Django auth; Zero Email Storage | Research + Q2(admin) |
| Owner role | Derived `@property` (`sub == OWNER_SUB_ID`), never persisted | Env var = single source of truth; no split-brain | Research |
| Account linking | Auth0 user-initiated linking (NOT auto-by-email) | Stable canonical `sub`; auto-by-email is an ATO vector | Research Q1 |
| `/api/me` payload | `nick` + `role` only (no `sub`) | Tightest leakage surface; SPA only needs nick+role | Plan (Q round 1) |
| RBAC proof | `require_owner` dependency + ONE demo route (`GET /api/users`) | Proves 401/403 contract; real actions are S-04 | Plan (Q round 1) |
| Import-linter | Add `layers` contract:2 (`bff` above the 3 domains) | Hard CI gate on `domain → bff`; machine-checks AGENTS.md §5 | Plan (Q round 2) |
| Django admin | Keep; add `is_staff`; override `create_superuser`; minimal UserAdmin | Dev GUI for inspecting/seeding users | Plan (Q round 2) |
| Docker scope | Dev-only: one `docker-compose.dev.yml` (runserver + qcluster + db volume + seed) | Covers interactive coding + live-reload; test/UAT runners later | Plan (Q round 2) |
| UAT test | Scaffolding ships now (markers, conftest, fixtures, CI shell); test deferred | Auth0-automation decision better made when prod flow settles | Research Q7 |
| UI in F-01 | Django templates; React in S-01 | Keeps F-01 focused on auth+RBAC; URL contract identical either way | Research Q2 |
| Prod platform | Render (AGENTS.md §1 authoritative) | User-confirmed; Railway refs in deploy-plan/infrastructure are stale | Plan |

## Scope

**In scope:** Custom `User` model + migration + `AUTH_USER_MODEL` swap; identity DTOs + pure services; `authlib` dep; env var surface; session/CSRF cookie hardening; `E001`/`W001` system checks; Authlib OAuth registry; `NinjaAPI(csrf=True)`; login/callback/logout + `/api/me` + demo `/api/users`; dev-bypass middleware; Django admin; welcome/login/main templates; pytest DEV/UAT markers + conftest + acceptance fixtures + conditional CI job; `Dockerfile` + `docker-compose.dev.yml` + dev seed + `.dockerignore`; import-linter contract:2.

**Out of scope:** React/Vite/SPA (S-01); dashboard content + nick-on-first-login UX (S-01); real owner actions — list/remove/invite-only bodies (S-04); the UAT Playwright test itself (later slice); account-linking code (Auth0 tenant gate, not Django); containerized test/UAT runners; audit logging; reconciling deploy-plan/infrastructure docs to Render.

## Architecture / Approach

Seven phases, each independently testable, mirroring the `vision/` domain's house style (pure `services.py`, Pydantic DTOs, `TextChoices`, `db_table` naming):

```
Phase 1  Identity domain (model, migration, DTOs, services, unit tests)
Phase 2  Config & hardening (authlib, env vars, cookies, E001/W001 checks)
Phase 3  BFF OAuth + RBAC (oauth.py, NinjaAPI, auth routes, /api/me, demo route, system test)
Phase 4  Dev experience (bypass middleware, Django admin)
Phase 5  Templates (welcome/login/main shells)
Phase 6  Test infra (DEV/UAT markers, conftest, acceptance fixtures, CI shell)
Phase 7  Docker dev env (Dockerfile, compose, seed, .dockerignore)
```

Boundary rule: `bff → domain` is the only allowed import direction (enforced by the new contract:2). Domains stay pure Python — no django-ninja, no HTTP.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Identity Domain Foundation | `User` model (derived role), migration, DTOs, services, unit tests | `AUTH_USER_MODEL` clean-slate swap ordering |
| 2. Configuration & Hardening | authlib dep, env vars, cookie hardening, E001/W001 checks | `E001` must be a plain `Error`, not `deploy=True` |
| 3. BFF OAuth + RBAC Plumbing | OAuth registry, NinjaAPI, auth routes, /api/me, demo route, system test | SameSite=Lax trap; `user.backend` before `login()` |
| 4. Dev Experience | Dev-bypass middleware, Django admin | Bypass must defer to real OAuth; admin needs usable password |
| 5. Templates | welcome/login/main shells | Dispatch view routing anonymous vs authed |
| 6. Test Infrastructure | pytest markers, autouse UAT skip, acceptance fixtures, CI shell | UAT-skip must be belt-and-suspenders |
| 7. Docker Dev Environment | Dockerfile, compose (runserver+qcluster+seed), .dockerignore | Live-reload bind mounts; idempotent seed |

**Prerequisites:** Auth0 tenant with an Application (client ID/secret/domain), Allowed Callback/Logout URLs configured, Database connection disabled, user-initiated account linking enabled. Local `.env` with `OWNER_SUB_ID`, Auth0 vars, and dev seed vars.

**Estimated effort:** ~7 sessions, one per phase; Phases 1–3 are the load-bearing core (model swap, security, OAuth), 4–7 are enabling.

## Open Risks & Assumptions

- **Auth0 `SameSite=Lax` dependency**: the OIDC callback *requires* `Lax` (not `Strict`) or the nonce is lost silently. Mitigated by a code comment + smoke test, but it's a documented real-world failure mode.
- **Account-linking setup is a manual tenant gate**: if the owner doesn't enable user-initiated linking in Auth0, the same human logging in via two connections gets two rows. Documented in the success checklist; no code can enforce it.
- **Dev-bypass residual gap**: the `E001` check runs before commands, not inside the WSGI/ASGI serving loop. Render sets `DEBUG=False` (mitigating), and the middleware's own DEBUG gate protects serving-time, but CI must run `manage.py check` as a gate.
- **Stale foundation docs**: `deploy-plan.md`/`infrastructure.md` reference Railway, not Render. Out of scope to fix here; flagged only.
- **UAT automation approach undecided**: whether the deferred UAT test uses a dedicated Auth0 Database connection or Mailtrap-backed magic link is deferred to the slice that writes it (research preserved as the starting point).

## Success Criteria (Summary)

- `docker compose -f docker-compose.dev.yml up` yields a live-reloading, seeded dev env where the full welcome → Auth0 → main chain works.
- `/api/me` returns nick+role (no sub); `/api/users` returns 401/403/200 by role — the RBAC contract is proven.
- `DEV_AUTH_BYPASS_SUB` + `DEBUG=False` → app refuses to boot (`E001`); empty `OWNER_SUB_ID` in prod → warning (`W001`).
- `uv run pytest` runs the DEV suite; UAT is opt-in and its test is deferred without breaking the build.
