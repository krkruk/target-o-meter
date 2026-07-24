# OAuth + Roles Scaffold (F-01) Implementation Plan

## Overview

Scaffold the full authentication vertical for Target-o-meter: an Auth0 OIDC (Authorization Code + PKCE) BFF flow, a custom zero-email identity model whose Owner/User role is *derived* from `OWNER_SUB_ID`, an RBAC enforcement skeleton proven by one demo owner route, a dev-only auth-bypass with a three-layer production guard, Django templates for the welcome/login/main shell, DEV/UAT test scaffolding (UAT test itself deferred), and a Docker dev environment with live-reload and seeded users. This unlocks S-01 (dashboard + nick-on-first-login) and S-04 (real owner actions).

## Current State Analysis

The codebase is at commit `eebc2f8` on `feature/oauth`. Verified against the live tree:

- **`src/domains/identity/`** — all five files (`models.py`, `services.py`, `ports.py`, `dtos.py`, `test_utils.py`) are docstring-only stubs. The app is registered (`apps.py:4-6` → `label='identity'`) and `migrations/__init__.py` exists with no `0001_initial.py` — so identity's first migration is free to define the swappable user model. No `__init__.py` exists for `tests/` yet.
- **`src/bff/`** — `__init__.py` is 0 bytes. The entire BFF layer (`api.py`, `oauth.py`, `routers/`, `urls.py`) is to be created.
- **`src/frontend/`** — exists but empty. Q2 decided F-01 uses Django templates; React lands in S-01. `django_vite` stays an unwired dev dependency.
- **`pyproject.toml`** — `django-ninja` is in the default group; **`authlib` is absent** (Q5 action item). `[tool.pytest.ini_options]` has only `DJANGO_SETTINGS_MODULE` + `pythonpath` (no markers, no `addopts`). `pytest-playwright` not listed.
- **`settings.py`** — `AUTH_USER_MODEL` absent; `TEMPLATES[0]['DIRS'] = []`; no session/CSRF cookie hardening block; `INSTALLED_APPS` includes `django.contrib.admin` (fate decided: keep + add `is_staff`); `MIDDLEWARE` already has `SessionMiddleware` + `AuthenticationMiddleware` (good for the cookie flow). DATABASES uses SQLite at `RAILWAY_VOLUME_MOUNT_PATH` (or `BASE_DIR`).
- **`urls.py`** — only `admin/`. No `/api/`, no app includes.
- **Tests** — `tests/system/` and `tests/acceptance/` exist but are empty. No root `conftest.py`. Only `src/domains/vision/tests/` has tests, using `pytestmark = pytest.mark.django_db`.
- **Docker** — zero Dockerfiles / compose files exist (`infrastructure.md` §"Out of Scope" listed Docker as future work). Genuinely new scope in this change.
- **DDD precedent to mirror** — `src/domains/vision/`: pure `services.py` (module docstring cites AGENTS.md §5), Pydantic DTOs, UUID cross-domain refs as plain `UUIDField` (not FK), `TextChoices` inner class, `class Meta: app_label=...; db_table=...`. `test_services_q2.py:90-110` is the pattern for testing owner-only logic (`PermissionError` on mismatch).

**Deployment note**: Prod target is **Render** (AGENTS.md §1 authoritative). The Railway references in `context/deployment/deploy-plan.md` and `context/foundation/infrastructure.md` are stale and out of scope for this change (F-01 doesn't ship a deploy). The session-cookie code comments will reference Render to match reality.

### Key Discoveries:

- **Derived role is the load-bearing decision** (research §7): `role` is a `@property` comparing `self.sub == os.environ["OWNER_SUB_ID"]`, never persisted. This makes the env var the single source of truth and avoids split-brain if a row is hand-edited. The `OWNER_SUB_ID`-empty startup guard (Q4) and the dev-bypass reuse of the same mechanism (Q6 synthesis) both depend on this design.
- **`AbstractBaseUser` clean-slate swap is safe** (research §7): the `auth_user` table is empty (no real user data), so setting `AUTH_USER_MODEL = "identity.User"` before the first identity migration + `rm db.sqlite3 && migrate` is correct. Do NOT use the two-stage data-preserving swap trick.
- **`SameSite=Lax` is mandatory** (research §5): the OAuth callback is a cross-site top-level GET navigation. `Strict` would suppress the `sessionid` cookie → Authlib finds no nonce → validation fails silently. This is a documented real-world OIDC failure mode; the plan requires a code comment + a smoke test.
- **`SessionAuth` trusts `request.user`** (research, dev-env §"Approach C"): django-ninja's `SessionAuth.authenticate()` only checks `request.user.is_authenticated` and never re-derives the user from the cookie. This is what makes the dev-bypass middleware work without fighting the framework.
- **Cross-connection `sub` stability requires Auth0 account linking** (research Q1): a human logging in via Google and via magic-link would otherwise get two different `sub` values → two rows. Auth0 user-initiated linking (NOT automatic-by-email — a real ATO vector) yields one canonical primary `sub`. The Django code is unchanged; this is a tenant setup gate documented in the success checklist.

## Desired End State

After this plan, a developer can:

1. **Run the app in Docker** — `docker compose -f docker-compose.dev.yml up` starts Django `runserver` (live-reloading on host edits to `src/`), a `qcluster` worker, and a seeded DB (one dev admin with Django-admin access, one Owner, one User).
2. **Sign in via Auth0** — clicking Login redirects to Auth0 Universal Login (social + magic-link; Database connection disabled), and the callback creates/resolves a `User` row keyed by the canonical `sub`, then redirects to `/` showing "logged in as {nick} ({role})".
3. **Hit `/api/me`** — returns `{authenticated, user: {nick, role}}` (no `sub`) for authenticated users, 401 otherwise. The SPA bootstrap contract is proven.
4. **Observe the 401/403 RBAC contract** — `GET /api/users` returns 401 when anonymous, 403 when authenticated-but-not-Owner, and an (empty) list when Owner.
5. **Develop without Auth0 locally** — with `DEV_AUTH_BYPASS_SUB` set and `DEBUG=True`, every request is auto-authenticated as that `sub`'s user (role derived via `OWNER_SUB_ID`, so setting the two env vars equal impersonates Owner).
6. **Trust the production guard** — if `DEV_AUTH_BYPASS_SUB` is set while `DEBUG=False`, `manage.py check` raises `target_o_meter.E001` and the app refuses to start; an empty `OWNER_SUB_ID` in prod emits a warning.
7. **Run tests** — `uv run pytest` runs the DEV suite (Auth0 bypassed by default); `uv run pytest -m uat` is skipped unless `RUN_UAT=1`. The UAT test itself is deferred to a later slice.

## What We're NOT Doing

- **React / Vite / SPA** — Q2: Django templates in F-01, React in S-01. `django_vite` stays an unwired dev dependency; no `package.json`, no `vite.config`.
- **Dashboard content / nick-on-first-login UX** — S-01. F-01's main page is a shell rendering "logged in as {nick} ({role})".
- **Real owner actions** (list-all-users bodies, remove-user, invite-only toggle) — S-04 (FR-003/004/005). F-01 ships only the `require_owner` dependency + ONE demo route proving the 403 contract.
- **The UAT Playwright test** — Q7: F-01 ships the UAT *scaffolding* (markers, conftest skip, fixtures, CI job shell) but NOT `tests/acceptance/test_uat_auth_real_auth0.py`. That test, and the Auth0-automation decision (dedicated UAT Database connection vs. Mailtrap magic link), lands in a later slice. Q8 (parameterize User+Owner creds) is a recorded intent for that slice.
- **Account-linking code** — Auth0 user-initiated linking is a tenant setup gate (documented in the checklist), not Django code. We store only the canonical `sub`.
- **Containerized test/UAT runners** — Q round 2: dev-only compose. `uv run pytest` still runs on the host. A test-image compose can land when CI does.
- **Audit logging** — `core.services.log_action` (referenced in AGENTS.md §6.2) does not exist; out of scope.
- **Reconciling `deploy-plan.md`/`infrastructure.md` to Render** — out of scope; those are foundation contracts and F-01 doesn't deploy. Flagged only.
- **Email scope on Auth0** — permanently disabled; `scope="openid profile"` (no `email`) is the defense-in-depth enforcement of Zero Email Storage.
- **POST-based logout** (plan-review F5) — F-01 ships logout as a GET link (template simplicity). S-01's SPA should re-implement logout as a POST with a CSRF token to close the GET-logout CSRF-soft vector (cross-site GET logout, stale-back-button). Recorded as an S-01 intent, not an F-01 code change.
- **Docker dev environment** (plan-review F6) — deferred to a dedicated change. F-01's dev-bypass middleware (Phase 4) already enables Auth0-free local development, so Docker is convenience, not a prerequisite for proving the auth/RBAC contract. See the (now-removed) former Phase 7 in git history for the intended shape.

## Implementation Approach

Six phases, each independently testable. Phase 1 (identity domain) is foundational — nothing else compiles without it. Phases 2–3 carry the load-bearing security work (cookie hardening, OAuth callback, production guard). Phases 4–5 make it usable (dev bypass, admin, templates). Phase 6 is test scaffolding. (A former Phase 7 — the Docker dev environment — was deferred to a dedicated change by plan-review F6; the dev-bypass middleware in Phase 4 already enables Auth0-free local dev, so Docker is convenience, not a prerequisite.)

**House style**: mirror `src/domains/vision/` for the identity domain (pure `services.py`, Pydantic DTOs, UUID PKs, `TextChoices`, `db_table` naming, module docstring citing AGENTS.md §5). One class per file with the `ports.py`/`dtos.py`/`UserManager`+`Role` carve-outs from `lessons.md`.

**Architecture enforcement**: the new `layers` import-linter contract (contract:2) makes `domain → bff` a CI failure. `bff → domain` is the only allowed direction (AGENTS.md §5/§6.2). Plan-review F3 — the exact contract body to add to `.importlinter` alongside the existing `contract:1` (`type=independence`):

```ini
[importlinter:contract:2]
name = BFF Above Domains
type = layers
layers =
    src.bff
    src.domains.core
    src.domains.identity
    src.domains.vision
```

`type = layers` REQUIRES the `layers =` key (mapping layer names to package globs) or import-linter fails to load the contract. The higher layer (`src.bff`) may import lower layers; the reverse is a violation. **Ordering note**: contract:2 can only PASS once `src.bff` is importable — i.e. at the end of Phase 3 (when `bff/api.py`, `bff/oauth.py`, and the routers exist). Phases 1–2 `uv run lint-imports` runs therefore assert contract:1 only; contract:2 is first asserted in Phase 3.4's success criteria.

## Critical Implementation Details

- **Clean-slate DB swap ordering** — `AUTH_USER_MODEL = "identity.User"` must be set in `settings.py` **before** `makemigrations identity` runs, and the swap is completed by `rm db.sqlite3 && uv run python src/manage.py migrate`. The existing `db.sqlite3` (if any) holds only `django_q`/admin/internal rows — safe to drop. Do NOT run `makemigrations` against the old user model.
- **`login()` without `authenticate()`** — the BFF callback calls `django.contrib.auth.login(request, user)` directly (Auth0 already proved identity; nothing to check a password against). Set `user.backend = "django.contrib.auth.backends.ModelBackend"` on the user before calling `login()`, so the session records a valid backend. If this is omitted and a second backend is ever added, `login()` raises `ValueError`.
- **`next` allowlist (open-redirect prevention)** — `/bff/login` accepts a `next` param and must validate it against an allowlist (e.g. `{"/"}`, or `url_has_allowed_host_and_scheme` from `django.utils.http`) before stashing it in the session and redirecting. Never redirect to an arbitrary user-supplied URL.
- **Logout `returnTo` encoding** — the Auth0 `/v2/logout` `returnTo` query param must be `urlencode(..., quote_via=quote_plus)`-encoded; mis-encoding makes Auth0 silently fall back to the first Allowed Logout URL.
- **`SameSite=Lax` is load-bearing** — see Key Discoveries. A code comment must explain why `Strict` breaks the OIDC callback. A smoke test asserts the cookie attribute at runtime.
- **Dev-bypass production guard is a plain `Error`, not `deploy=True`** — a `@register(..., deploy=True)` check only runs under `manage.py check --deploy` and would NOT fire on `runserver`/WSGI boot. `target_o_meter.E001` must be a plain `@register(Tags.security)` `Error` so it prevents Django commands from running at all. Register it via `from . import checks  # noqa` at the bottom of `settings.py`.
- **`create_superuser` must set a usable password + `is_staff`** — overrides the research §7 sketch (which delegated to `create_user`, leaving an unusable password). Django admin login requires both. Only the Docker dev-seed path calls `create_superuser`; OAuth users never do.

## Phase 1: Identity Domain Foundation

### Overview

Build the pure-Python identity domain: the swappable `User` model with a derived role, its first migration, Pydantic DTOs, and the pure services the BFF will call. Nothing in this phase imports django-ninja or touches HTTP — it obeys AGENTS.md §5 exactly as `vision/` does.

### Changes Required:

#### 1.1 Custom User model

**File**: `src/domains/identity/models.py`

**Intent**: Replace the docstring-only stub with the swappable user model keyed by Auth0's canonical `sub`. This is the zero-email identity anchor the whole auth vertical depends on.

**Contract**: `AbstractBaseUser` subclass named `User`, **with `PermissionsMixin`** (plan-review F1: `AbstractBaseUser` alone lacks `has_perm`/`has_module_perms`, so Phase 4.2's registered `identity_user` ModelAdmin would raise `AttributeError` or silently disappear from the admin index — `ModelAdmin.has_view_permission`/`has_module_permission` call those methods, which exist only on `PermissionsMixin`). Fields: `id` (UUID PK, `default=uuid.uuid4`), `sub` (`CharField(max_length=255, unique=True)` — the OIDC `sub`, opaque), `nick` (`CharField(max_length=64)`, case-insensitive unique via `UniqueConstraint(Lower("nick"))` with explicit `violation_error_message`), `is_staff` (`BooleanField(default=False)` — always False for OAuth users; True only for the seeded dev admin). `last_login` + `is_superuser` (from `PermissionsMixin`) inherited (kept — Q3). `USERNAME_FIELD = "sub"`, `REQUIRED_FIELDS = []`. Inner `class Role(models.TextChoices): OWNER="owner"; USER="user"`. Derived `@property role` compares `self.sub == os.environ.get("OWNER_SUB_ID", "")` (fails closed: empty env → never Owner). Derived `@property is_owner`. `class Meta: app_label="identity"; db_table="identity_user"` plus the nick CI-unique constraint. Module-level `Role` and a private `_generated_nick()` (`f"shooter-{uuid.uuid4().hex[:8]}"`) are the F-01 fallback before S-01's nick-prompt UX. One-class-per-file carve-out from `lessons.md` covers `UserManager` + `Role` living alongside `User`.

#### 1.2 UserManager

**File**: `src/domains/identity/models.py` (same file, lesson carve-out)

**Intent**: Provide the manager Django requires for a custom user, and the dev-admin seed path.

**Contract**: `UserManager(BaseUserManager)` with `use_in_migrations = True`. `create_user(sub, nick="", **extra)` raises `ValueError` if `sub` is empty, defaults `nick` to `_generated_nick()` when blank, calls `set_unusable_password()`, saves. `create_superuser(sub, nick="", **extra)` sets a **usable** password (from `extra.pop("password")`, required) and `is_staff=True` before delegating to `create_user`'s save path — this diverges from the research §7 sketch (which left the password unusable) because the dev admin needs a real password for Django admin login. The role is NOT conferred here (Owner is derived from `OWNER_SUB_ID`, single source of truth).

#### 1.3 First migration + AUTH_USER_MODEL swap

**Files**: `src/target_o_meter/settings.py`, `src/domains/identity/migrations/0001_initial.py`

**Intent**: Wire the custom user as Django's auth model and generate identity's first migration.

**Contract**: In `settings.py`, add `AUTH_USER_MODEL = "identity.User"` (app **label**, not dotted path — matches `apps.py:5`). Then run `uv run python src/manage.py makemigrations identity` to generate `0001_initial.py` (do not hand-write it). Complete the clean-slate swap: `rm -f db.sqlite3 && uv run python src/manage.py migrate`. See Critical Implementation Details for the ordering constraint.

#### 1.4 Identity DTOs

**File**: `src/domains/identity/dtos.py`

**Intent**: Define the typed boundary the BFF consumes (DTOs only — never return ORM objects across boundaries, AGENTS.md §5).

**Contract**: Pydantic `BaseModel` classes mirroring `vision/dtos.py:37-51`. `UserContextDTO` (fields: `user_uuid: UUID`, `sub: str`, `nick: str`, `role: str`, `is_owner: bool`). `MeOut` (the `/api/me` response: `authenticated: bool`, `user: UserOut | None`). `UserOut` (fields: `nick: str`, `role: str` — **no `sub`**, per Q round 1).

#### 1.5 Pure identity services

**File**: `src/domains/identity/services.py`

**Intent**: Pure-Python services the BFF calls; module docstring cites AGENTS.md §5 (no django-ninja, no HTTP), mirroring `vision/services.py:1-323`.

**Contract**: `get_or_create_user_by_sub(sub: str) -> UserContextDTO` — wraps `User.objects.get_or_create(sub=..., defaults={"nick": _generated_nick()})`, returns the DTO. `get_user_context(sub: str) -> UserContextDTO` — fetches by `sub`, returns DTO (pattern from `vision/services.py:238-255`). `is_owner(dto: UserContextDTO) -> bool` — thin read of `dto.is_owner`. `list_users() -> list[UserOut]` — returns `UserOut` DTOs (no `sub`); the demo owner route's backing call. All take primitives, return DTOs.

#### 1.6 Domain unit tests

**Files**: `src/domains/identity/tests/__init__.py` (empty), `src/domains/identity/tests/conftest.py`, `src/domains/identity/tests/test_services.py`

**Intent**: Cover the derived-role logic, nick CI-uniqueness, and the get-or-create path. Mirror `vision/tests/test_services_q2.py:90-110` and `vision/tests/conftest.py`.

**Contract**: `pytestmark = pytest.mark.django_db`. Tests: (a) `role` property returns `OWNER` when `OWNER_SUB_ID` env matches `sub`, else `USER`; (b) empty `OWNER_SUB_ID` → never Owner (fails closed); (c) nick CI-uniqueness (`"Bob"` then `"bob"` raises `IntegrityError`); (d) `get_or_create_user_by_sub` creates on first call, returns existing on second; (e) `list_users()` returns no `sub` field. Conftest provides a User seeder via `identity/test_utils.py` (AGENTS.md §5 — no `factory_boy` against models).

#### 1.7 test_utils seeder

**File**: `src/domains/identity/test_utils.py`

**Intent**: Provide the AGENTS-mandated seeder so tests (and the Docker dev seed) don't touch the ORM directly.

**Contract**: `make_user(*, sub, nick=None, is_staff=False) -> User` and `make_owner(sub) -> User` (sets `OWNER_SUB_ID` in the test env then creates). Mirrors the role of `vision/test_utils.py`.

### Success Criteria:

#### Automated Verification:

- Migration applies cleanly: `rm -f db.sqlite3 && uv run python src/manage.py migrate`
- Identity unit tests pass: `uv run pytest src/domains/identity/tests/`
- System check passes: `uv run python src/manage.py check`
- Lint passes: `uv run ruff check .`
- Import-linter passes (contract:1 still green): `uv run lint-imports`

#### Manual Verification:

- `uv run python src/manage.py shell -c "from django.contrib.auth import get_user_model; print(get_user_model())"` prints `<class 'src.domains.identity.models.User'>`
- `showmigrations identity` shows `0001_initial` applied

**Implementation Note**: After this phase and all automated verification passes, pause for manual confirmation that the model swap worked (shell check above) before proceeding.

---

## Phase 2: Configuration & Hardening

### Overview

Land the dependency, environment, cookie-hardening, and system-check work. No behavior is user-visible yet, but this phase carries the production-safety guarantees (`E001`, `W001`) and the SameSite=Lax invariant the OAuth callback depends on.

### Changes Required:

#### 2.1 authlib dependency

**File**: `pyproject.toml`

**Intent**: Add the OIDC client library the BFF imports (Q5 — it is NOT currently declared).

**Contract**: Add `"authlib>=1.6.6"` to `[dependency-groups].default`. Run `uv lock` to update `uv.lock`. Verify `uv run python -c "from authlib.integrations.django_client import OAuth"` succeeds with no deprecation warning.

#### 2.2 Environment variable surface

**Files**: `.env.example`, `src/target_o_meter/settings.py`

**Intent**: Document and read every new env var, following the existing UPPER_SNAKE + `os.environ.get(NAME, default)` convention.

**Contract**: `.env.example` gains documented blocks for `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, `AUTH0_DOMAIN`, `OWNER_SUB_ID=` (with the impersonation comment), `DEV_AUTH_BYPASS_SUB=` (with the safety comment citing `target_o_meter.E001`), and optional `SECURE_COOKIES=False`. In `settings.py`, read each into module-level constants with safe defaults (empty string for secrets). Add a `DEV_ADMIN_SUB`, `DEV_ADMIN_PASSWORD`, `DEV_ADMIN_NICK` block for the Phase 4/7 dev seed (documented as dev-only).

#### 2.3 Session & CSRF cookie hardening

**File**: `src/target_o_meter/settings.py`

**Intent**: Enforce HttpOnly/Secure/SameSite per the BFF pattern (research §5), with the SameSite=Lax trap documented in-code.

**Contract**: Add (env-gated where noted): `SESSION_COOKIE_HTTPONLY = True`, `SESSION_COOKIE_SECURE = SECURE_COOKIES` (env bool), `SESSION_COOKIE_SAMESITE = "Lax"` with a comment explaining why `Strict` breaks the OIDC callback (cross-site redirect drops the `sessionid` → Authlib loses the nonce), `SESSION_COOKIE_AGE = 60*60*8` (8h — bounds token exposure), `CSRF_COOKIE_SAMESITE = "Lax"`, `CSRF_COOKIE_SECURE = SECURE_COOKIES`, `CSRF_COOKIE_HTTPONLY = False` (SPA must read `csrftoken` for future POSTs). `NinjaAPI(csrf=True)` is wired in Phase 3.

#### 2.4 Production-safety system checks

**Files**: `src/target_o_meter/checks.py`, `src/target_o_meter/settings.py`

**Intent**: Two layered guards — a hard `Error` that blocks boot if the dev bypass is active in a prod-shaped config, and a `Warning` that surfaces an empty Owner env in prod.

**Contract**: `checks.py` defines `check_dev_auth_bypass_not_in_prod` (`@register(Tags.security)`, NOT `deploy=True`) → raises `Error(id="target_o_meter.E001", ...)` when `DEV_AUTH_BYPASS_SUB` is set AND `DEBUG is False`. Also `check_owner_sub_id_set` → `Warning(id="target_o_meter.W001", ...)` when `OWNER_SUB_ID` is empty AND `DEBUG is False`. Register both by adding `from . import checks  # noqa: F401` at the bottom of `settings.py`. See Critical Implementation Details for why `deploy=True` is wrong.

#### 2.5 roadmap.md email→sub correction

**File**: `context/foundation/roadmap.md`

**Intent**: Resolve the internal inconsistency (research synthesis): `roadmap.md:64` says "owner determinable via a configured designated **email**", which contradicts AGENTS.md §2 "Zero Email Storage" and this plan's `OWNER_SUB_ID` design.

**Contract**: Change the "email" wording to "sub" (`OWNER_SUB_ID` env var). Minimal edit, single line. (Foundation contract — change only this one inconsistent line; do not restructure the roadmap.)

### Success Criteria:

#### Automated Verification:

- `uv run python src/manage.py check` passes (no errors/warnings in dev with env unset)
- Setting `DEBUG=False` + `DEV_AUTH_BYPASS_SUB=x` + `uv run python src/manage.py check` exits non-zero with `target_o_meter.E001`
- Setting `DEBUG=False` + empty `OWNER_SUB_ID` + `uv run python src/manage.py check` emits `target_o_meter.W001`
- `uv run ruff check .` passes

#### Manual Verification:

- `uv run python src/manage.py check --deploy` runs without our new checks silently no-op'ing
- Confirm `.env.example` documents every new var with a comment

**Implementation Note**: Pause for manual confirmation that both system checks fire correctly before proceeding.

---

## Phase 3: BFF OAuth + RBAC Plumbing

### Overview

Birth the entire BFF layer: the Authlib OAuth registry, the django-ninja API with CSRF on, the auth routes (login/callback/logout), `/api/me`, and the demo owner route that proves the 401/403 contract. This is the phase that makes the auth vertical actually work.

### Changes Required:

#### 3.1 Authlib OAuth registry

**File**: `src/bff/oauth.py` (new)

**Intent**: Register the Auth0 OIDC client with PKCE. HTTP-adjacent → BFF per AGENTS.md §5.

**Contract**: `oauth = OAuth()`; `oauth.register("auth0", client_id=AUTH0_CLIENT_ID, client_secret=AUTH0_CLIENT_SECRET, client_kwargs={"scope": "openid profile", "code_challenge_method": "S256"}, server_metadata_url=f"https://{AUTH0_DOMAIN}/.well-known/openid-configuration")`. No `email` scope (Zero Email Storage enforcement — research §3).

#### 3.2 django-ninja API + auth callables

**Files**: `src/bff/api.py` (new), `src/bff/routers/__init__.py` (empty)

**Intent**: Create the NinjaAPI instance with CSRF on and the auth primitives every router shares.

**Contract**: `api = NinjaAPI(csrf=True)`. `session_auth = SessionAuth()` (django-ninja's built-in — reads `sessionid`, checks `request.user.is_authenticated`). `require_owner(request)` dependency: resolves `get_user_context(str(request.user.sub))`, raises `HttpError(403, "Owner privileges required")` if not owner, returns the DTO. 401 vs 403 contract: falsy return from `auth` callable → 401; `HttpError(403)` inside a dependency → 403.

#### 3.3 Auth routes (login / callback / logout)

**File**: `src/bff/routers/auth_routes.py` (new)

**Intent**: The OIDC redirect chain, server-side. Login stashes `next` (allowlisted), redirects to Auth0 `/authorize`; callback validates the token (Authlib auto-validates signature/iss/aud/nonce/exp), resolves/creates the user, calls `login()`, redirects to `next`; logout clears the Django session and redirects to Auth0 `/v2/logout`.

**Contract**: `login(request)`: read `next` param, validate via `url_has_allowed_host_and_scheme` (open-redirect prevention), stash in `request.session["oauth_next"]`, call `oauth.auth0.authorize_redirect(request, redirect_uri=request.build_absolute_uri(reverse("bff:callback")))`. `callback(request)`: `token = oauth.auth0.authorize_access_token(request)` (Authlib validates); extract `sub` from `token["userinfo"]`; `user = get_or_create_user_by_sub(sub)`; set `user.backend = "django.contrib.auth.backends.ModelBackend"`; `django.contrib.auth.login(request, user)`; redirect to the allowlisted `next` (default `/`). `logout(request)`: `request.session.clear()`; `redirect("https://{AUTH0_DOMAIN}/v2/logout?" + urlencode({"returnTo": request.build_absolute_uri(reverse("index")), "client_id": AUTH0_CLIENT_ID}, quote_via=quote_plus))`. See Critical Implementation Details for the `user.backend` and `returnTo`-encoding gotchas.

#### 3.4 /api/me endpoint

**File**: `src/bff/routers/session_routes.py` (new)

**Intent**: The SPA auth-state bootstrap — returns the logged-in user's nick+role (no sub) or 401.

**Contract**: `@router.get("/me", auth=session_auth, response={200: MeOut})` (plan-review F4: do NOT declare `401: MeOut` — a failed `auth` callable raises `AuthenticationError`, routed through django-ninja's default handler → returns `{"detail": "Unauthorized"}`, never a serialized `MeOut`. Declaring `401: MeOut` would mislead clients. Desired End State #3 only requires "401 otherwise," no body shape). Resolves `get_user_context(str(request.user.sub))`, returns `200, MeOut(authenticated=True, user=UserOut(nick=dto.nick, role=dto.role))`. GET needs no CSRF token. `UserOut` has no `sub` field (Q round 1).

#### 3.5 Demo owner route

**File**: `src/bff/routers/owner_routes.py` (new)

**Intent**: Prove the 401/403 RBAC contract end-to-end with one real route. Real list/remove/invite-only logic is S-04.

**Contract**: `@router.get("/users", auth=[session_auth, require_owner])`. Backed by `identity.services.list_users()` → `list[UserOut]`. Returns an empty list until S-04 adds real data. Anonymous → 401 (session_auth falsy); authenticated non-Owner → 403 (`require_owner` raises); Owner → 200 `[]`.

#### 3.6 BFF URL wiring + index view

**Files**: `src/bff/urls.py` (new), `src/target_o_meter/urls.py`

**Intent**: Mount the BFF (auth routes + ninja API) under the project URLconf. Phase 5 adds the template catch-all.

**Contract**: `src/bff/urls.py` defines `urlpatterns` including `auth_routes.router` (app_name `"bff"`, names: `login`, `callback`, `logout`), mounts `api` under `api/` (so `/api/me`, `/api/users`), and an `index` view (redirects to `/` — the template route lives in Phase 5). `src/target_o_meter/urls.py` adds `path("", include("src.bff.urls"))` (BFF/auth/api first; SPA catch-all last per research §12).

#### 3.7 System test: auth-bootstrap + RBAC contract

**Files**: `tests/system/__init__.py` (empty — check vision style first), `tests/system/conftest.py`, `tests/system/test_auth_flow.py`

**Intent**: The repo's first system test. Asserts the `/api/me` 200/401 split and the `/api/users` 401/403/200 split using the dev-bypass (set `DEV_AUTH_BYPASS_SUB` in the test env, impersonate Owner then User). No real Auth0 call (UAT is deferred).

**Contract**: `pytestmark = [pytest.mark.django_db, pytest.mark.dev]`. Fixtures set `OWNER_SUB_ID` and `DEV_AUTH_BYPASS_SUB` via `monkeypatch.setenv`, seed users, and use `client.force_login()` or the bypass middleware to authenticate. Assertions: `/api/me` → 401 anonymous, 200 authed with correct nick/role and no `sub` key; `/api/users` → 401 anonymous, 403 User, 200 `[]` Owner. Conftest provides the authenticated-client fixtures (via `identity/test_utils.py`).

### Success Criteria:

#### Automated Verification:

- System test passes: `uv run pytest tests/system/test_auth_flow.py`
- System check passes: `uv run python src/manage.py check`
- Import-linter passes (contract:1 + contract:2): `uv run lint-imports`
- `uv run ruff check .` passes

#### Manual Verification:

- `runserver` boots; `curl -i http://localhost:8000/api/me` returns 401 (no session)
- (Full OAuth happy-path requires Auth0 creds — covered by the deferred UAT test; manual smoke in dev with bypass set: `/api/me` returns 200, `/api/users` returns 403 as User / 200 as Owner)

**Implementation Note**: Pause for manual confirmation that the 401/403 contract holds via curl before proceeding.

---

## Phase 4: Dev Experience

### Overview

Make local development fast (auth bypass) and give the Owner a Django admin view for inspecting/seeding users. Both depend on Phase 1's model and Phase 2's checks.

### Changes Required:

#### 4.1 Dev-auth-bypass middleware

**File**: `src/target_o_meter/dev_auth_bypass.py` (new)

**Intent**: Skip the Auth0 dance locally by auto-authenticating as a `sub` from the env (Q6 synthesis: reuses `OWNER_SUB_ID` as the single role source — set the two equal to impersonate Owner).

**Contract**: `DevAuthBypassMiddleware(MiddlewareMixin)`. `process_request`: **first line must be `if not settings.DEBUG: return`** (plan-review F2: the E001 system check only runs at `manage.py check`/`runserver`, NOT in the gunicorn serving loop on Render, so the middleware must self-gate on `DEBUG=False` — otherwise a misconfigured prod env with `DEV_AUTH_BYPASS_SUB` set serves with the bypass live, and setting it equal to `OWNER_SUB_ID` impersonates Owner); then if `DEV_AUTH_BYPASS_SUB` unset → return; if `request.user.is_authenticated` → return (real OAuth login wins); else set `request.user = _get_dev_user()`. `_get_dev_user()` is a module-level-cached `User.objects.get_or_create(sub=DEV_AUTH_BYPASS_SUB, defaults={"nick": "dev-"+sub[:8]})`. Stateless — never touches the session, never calls `login()`. Register in `MIDDLEWARE` immediately after `AuthenticationMiddleware`. See Key Discoveries for why `SessionAuth` makes this work.

#### 4.2 Django admin integration

**Files**: `src/domains/identity/admin.py` (new), `src/target_o_meter/settings.py`

**Intent**: Give the seeded dev admin a GUI for inspecting/seeding `identity_user` rows (the Option 3 + is_staff decision).

**Contract**: `admin.py` registers a minimal read-mostly `UserAdmin(ModelAdmin)` over `User`: `list_display = ("sub", "nick", "is_staff", "last_login")`, `search_fields = ("sub", "nick")`, `readonly_fields = ("role", "is_owner")` (derived — display only), `list_filter = ("is_staff",)`. `is_staff` settable so the dev admin can promote another local admin if needed; `role`/`is_owner` never editable (they're derived from env). No password-change flow exposed in admin (OAuth users have unusable passwords; the dev admin password is set via the Phase 7 seed).

### Success Criteria:

#### Automated Verification:

- System check passes: `uv run python src/manage.py check`
- Existing tests still pass: `uv run pytest`
- `uv run ruff check .` passes

#### Manual Verification:

- With `DEV_AUTH_BYPASS_SUB` set + `DEBUG=True`: `curl http://localhost:8000/api/me` returns 200 as the dev user without any Auth0 call
- Log into `/admin/` as the seeded dev admin; `identity_user` rows are visible and searchable; `role`/`is_owner` are read-only

**Implementation Note**: Pause for manual confirmation that the bypass + admin both work before proceeding.

---

## Phase 5: Templates (Welcome / Login / Main)

### Overview

Ship the three UI states as plain Django templates (Q2 — React in S-01). The full URL contract is identical either way, so S-01 swaps templates for React with zero endpoint churn.

### Changes Required:

#### 5.1 Template dir config + base template

**Files**: `src/target_o_meter/settings.py`, `templates/base.html` (new)

**Intent**: Point Django at a project-level `templates/` dir (currently `TEMPLATES[0]['DIRS'] = []`) and provide a base shell.

**Contract**: `settings.py` sets `TEMPLATES[0]['DIRS'] = [BASE_DIR / "templates"]`. `base.html` defines a minimal block skeleton (`{% block title %}`, `{% block content %}`).

#### 5.2 Welcome, login-button, and main shells

**Files**: `templates/welcome.html`, `templates/main.html` (new), `src/bff/views.py` (new or in `urls.py`)

**Intent**: Render the three states. Welcome (unauthenticated) shows a Login button → `/bff/login`; Main shows "logged in as {nick} ({role})" + a Logout button → `/bff/logout`.

**Contract**: `welcome.html` extends `base.html`, renders a Login button linking to `{% url "bff:login" %}`. `main.html` extends `base.html`, reads `request.user.nick` and `request.user.role`, renders "logged in as {nick} ({role})" and a Logout button. A view dispatches: anonymous → `welcome.html`, authenticated → `main.html`. Routed at `/` (the `index` route from Phase 3.6). F-01 ships only this shell text — dashboard content is S-01.

### Success Criteria:

#### Automated Verification:

- System check passes: `uv run python src/manage.py check`
- `uv run ruff check .` passes
- Existing tests still pass: `uv run pytest`

#### Manual Verification:

- Visit `/` unauthenticated → welcome page with Login button
- Click Login → (with bypass) lands on main page showing "logged in as {nick} ({role})"
- Click Logout → returns to welcome

**Implementation Note**: Pause for manual confirmation of the three-state navigation before proceeding.

---

## Phase 6: Test Infrastructure (DEV/UAT Scaffolding)

### Overview

Stand up the pytest marker system, the autouse UAT-skip, the acceptance-test fixtures, and the conditional CI job — but NOT the UAT test itself (Q7 deferred). A later slice drops in the test and it just works against this scaffolding.

### Changes Required:

#### 6.1 pytest markers + addopts

**File**: `pyproject.toml`

**Intent**: Make `uv run pytest` never fire Auth0 by default, with explicit opt-in for UAT.

**Contract**: `[tool.pytest.ini_options]` adds `addopts = ["--strict-markers", "--strict-config", "-m", "not uat"]` and `markers = ["dev: fast, Auth0-bypassed tests (default).", "uat: slow acceptance tests hitting REAL Auth0; skipped unless RUN_UAT=1."]`.

#### 6.2 Root conftest (autouse UAT skip)

**File**: `conftest.py` (repo root, new)

**Intent**: Belt-and-suspenders — even `pytest -m uat` skips unless `RUN_UAT=1`.

**Contract**: `pytest_configure` registers both markers (in addition to pyproject). `_skip_uat_unless_opted_in` autouse fixture: if the node has the `uat` marker and `RUN_UAT` env is unset, `pytest.skip("UAT skipped: set RUN_UAT=1 (requires real Auth0 creds).")`.

#### 6.3 Acceptance conftest (deferred-test fixtures)

**File**: `tests/acceptance/conftest.py` (new)

**Intent**: Provide the fixtures the later UAT test will consume, with skip-on-missing so a missing secret never turns the build red.

**Contract**: `uat_auth0_creds` fixture (parametrizable across `"user"`/`"owner"` per Q8): reads `AUTH0_UAT_*` env vars, `pytest.skip`s if any are empty. `uat_base_url` fixture. No test file yet (Q7 — deferred).

#### 6.4 pytest-playwright dep + .env.uat gitignore

**Files**: `pyproject.toml`, `.gitignore`

**Intent**: Add the runner for the deferred UAT test; prevent UAT secrets from leaking.

**Contract**: Add `"pytest-playwright>=0.5.0"` to `[dependency-groups].system-test`. Add `.env.uat` to `.gitignore`.

#### 6.5 Conditional UAT CI job (shell only)

**File**: `.github/workflows/uat.yml` (new — first CI file in the repo)

**Intent**: A CI job that will run UAT when enabled, but is a no-op until a UAT test exists.

**Contract**: A job gated on `vars.UAT_ENABLED == 'true'` AND same-repo origin, running `RUN_UAT=1 uv run pytest -m uat`. The per-test skip (6.3) makes it a no-op until the test lands. Shell-only in F-01.

### Success Criteria:

#### Automated Verification:

- `uv run pytest` runs the DEV suite, skips any `uat`-marked test (none exist yet, but the mechanism is verified by a temporary throwaway marker test removed before commit)
- `uv run pytest -m uat` skips with the documented message (no `RUN_UAT`)
- `uv run ruff check .` passes
- `uv run lint-imports` passes

#### Manual Verification:

- Confirm `.env.uat` is gitignored: `git status` ignores a created `.env.uat`
- Confirm the CI YAML is valid: `python -c "import yaml; yaml.safe_load(open('.github/workflows/uat.yml'))"`

**Implementation Note**: Pause for manual confirmation that the UAT-skip mechanism works before proceeding. Phases 1–6 complete the F-01 scope (auth vertical + RBAC proof + dev bypass + test scaffolding).

---

## Deferred: Docker Dev Environment (moved out by plan-review F6)

> **Status**: NOT part of F-01. Deferred to a dedicated change. F-01's dev-bypass middleware (Phase 4) already enables Auth0-free local development (`DEV_AUTH_BYPASS_SUB` + `DEBUG=True`), so Docker is convenience, not a prerequisite for proving the auth/RBAC contract. The content below is preserved as the starting point for that future change — it is out of scope here and not tracked in the Progress section.

### Overview

### Changes Required:

#### 7.1 Dockerfile

**File**: `Dockerfile` (new)

**Intent**: A dev image with Python 3.14, uv, and the opencv/system deps the vision domain needs.

**Contract**: `FROM python:3.14-slim`. Install system deps for `opencv-python-headless` (per `infrastructure.md` risk register: pre-build opencv into the image). Install `uv`. `COPY pyproject.toml uv.lock` then `uv sync` (leverages the Docker cache; `src/` is bind-mounted at runtime, not copied, for live-reload). `WORKDIR /app`. Entrypoint deferred to compose.

#### 7.2 Dev compose file

**File**: `docker-compose.dev.yml` (new)

**Intent**: One command (`docker compose -f docker-compose.dev.yml up`) gives a fully-seeded, live-reloading dev environment.

**Contract**: Two services sharing the same image: `web` (runs `uv run python src/manage.py runserver 0.0.0.0:8000`, bind-mounts `./src` and `./templates` for live-reload, exposes `:8000`, depends_on `worker`) and `worker` (runs `uv run python src/manage.py qcluster`, same bind mounts). One named volume for the SQLite DB (mounted where `DATABASES['NAME']` points). An `entrypoint` script runs `migrate` then the dev seed before `runserver`/`qcluster`. Env vars sourced from `.env` (including `DEBUG=True`, `DEV_AUTH_BYPASS_SUB`, `OWNER_SUB_ID`, `DEV_ADMIN_*`, Auth0 vars for real-flow testing). `DEV_AUTH_BYPASS_SUB` set by default in dev so the app is usable without Auth0.

#### 7.3 Dev seed entrypoint

**File**: `docker/dev-seed.sh` (new) or inline in compose

**Intent**: Idempotently seed the dev admin (staff + usable password for Django admin) plus an Owner and a User row for role testing.

**Contract**: Runs `uv run python src/manage.py migrate` unconditionally. Then a `manage.py shell`-invoked seed (or a management command) that: `create_superuser(sub=DEV_ADMIN_SUB, nick=DEV_ADMIN_NICK, password=DEV_ADMIN_PASSWORD)` if not exists; `get_or_create_user_by_sub(OWNER_SUB_ID)` (nick `"dev-owner"`); `get_or_create_user_by_sub("dev-user-sub")` (nick `"dev-user"`). Idempotent (safe to re-run on every `up`).

#### 7.4 .dockerignore

**File**: `.dockerignore` (new)

**Intent**: Keep the build context lean and avoid copying host artifacts.

**Contract**: Ignore `.venv/`, `node_modules/`, `.git/`, `*.sqlite3`, `staticfiles/`, `resources/`, `cv/` (frozen sandbox), `context/` (docs, not runtime).

### Success Criteria:

#### Automated Verification:

- `docker compose -f docker-compose.dev.yml config` validates the compose file
- `docker build -t target-o-meter-dev .` succeeds
- `uv run ruff check .` passes (the seed script, if Python, is linted)

#### Manual Verification:

- `docker compose -f docker-compose.dev.yml up` brings up `web` + `worker` cleanly
- Editing a file in `src/` triggers a `runserver` reload (live-reload verified)
- `/admin/` is reachable; logging in as the seeded dev admin works; `identity_user` shows the seeded Owner + User rows
- `/api/me` returns 200 as the dev user (bypass active); `/api/users` returns 403 when impersonating User, 200 when impersonating Owner (via `DEV_AUTH_BYPASS_SUB` vs `OWNER_SUB_ID`)
- Visiting `/` shows the welcome page; the full Login → Auth0 → callback → main chain works end-to-end with real Auth0 creds in `.env`

**Implementation Note**: Pause for manual confirmation that the full Docker dev loop works before considering this change done.

---

## Testing Strategy

### Unit Tests:

- Identity domain (Phase 1.6): derived role (Owner match, empty-env fail-closed, User default), nick CI-uniqueness, `get_or_create_user_by_sub` create/return-existing, `list_users` no-sub.

### Integration / System Tests:

- `tests/system/test_auth_flow.py` (Phase 3.7): `/api/me` 401/200 split, `/api/users` 401/403/200 split, via the dev-bypass (no real Auth0). The repo's first system test — establishes the `tests/system/conftest.py` pattern.

### Deferred (UAT — later slice):

- `tests/acceptance/test_uat_auth_real_auth0.py`: real Auth0 happy-path via Playwright, parameterized User+Owner (Q8). F-01 ships the fixtures/conftest it will consume, not the test.

### Manual Testing Steps:

1. Phase 1: shell check that `get_user_model()` is the identity User.
2. Phase 2: confirm both system checks (`E001`, `W001`) fire under the right env combos.
3. Phase 3: curl `/api/me` and `/api/users` to observe 401/403/200.
4. Phase 4: log into `/admin/` as the seeded dev admin.
5. Phase 5: click through welcome → login → main → logout.
6. (Phase 7 / Docker deferred — see "Deferred" section; its manual loop is tracked by the future change that picks it up.)

## Performance Considerations

- The derived `role` property does one string comparison per request against a value already on `request.user` — negligible.
- `last_login` triggers one DB UPDATE per login (Q3 accepted — negligible at MVP scale).
- `_get_dev_user()` caches the dev user at module level (immutable for process lifetime) — no per-request DB hit from the bypass.
- `list_users()` (demo route) returns an empty list until S-04 — no scaling concern now.

## Migration Notes

- **`AUTH_USER_MODEL` swap is clean-slate**: the existing `db.sqlite3` holds only `django_q`/admin/internal rows (no real user data). `rm -f db.sqlite3 && migrate` is the documented path (research §7, Django #25313). Do NOT use the two-stage data-preserving swap.
- **No data migration** — there is no prior user data to migrate.
- **Rollback**: revert the `AUTH_USER_MODEL` setting, restore the old `db.sqlite3` from git/VCS (it's gitignored, so only if a backup exists). In practice the swap is one-way for this project.
- **Release-gate requirement (plan-review F2)**: `E001` fires at `manage.py check` time, not in the gunicorn serving loop. Render runs gunicorn, so `manage.py check` is NOT automatically part of prod boot. The DevAuthBypassMiddleware DEBUG gate (Phase 4.1) is the serving-layer guard, but the deploy/release pipeline MUST also run `uv run python src/manage.py check` as a gate (exit-non-zero on `E001`/`W001`) before promoting a release. Wiring that CI/release step is out of scope for F-01 (Phase 6 ships only a UAT CI shell) — flag it for the first change that touches deploy/CI.

## References

- Research: `context/changes/oauth-roles-scaffold/research.md` (Q1–Q8 all resolved)
- Identity DDD precedent: `src/domains/vision/models.py:13-65`, `src/domains/vision/services.py:1-323`, `src/domains/vision/dtos.py:37-51`
- Owner-only test pattern: `src/domains/vision/tests/test_services_q2.py:90-110`
- House-style lesson: `context/foundation/lessons.md` ("One class per file" + carve-outs)
- Auth0 Django quickstart: https://auth0.com/docs/quickstart/webapp/django
- django-ninja SessionAuth/CSRF: https://django-ninja.dev/guides/authentication/, https://django-ninja.dev/reference/csrf/

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Identity Domain Foundation

#### Automated

- [x] 1.1 Migration applies cleanly (`rm -f db.sqlite3 && uv run python src/manage.py migrate`) — 176ff75
- [x] 1.2 Identity unit tests pass (`uv run pytest src/domains/identity/tests/`) — 176ff75
- [x] 1.3 System check passes (`uv run python src/manage.py check`) — 176ff75
- [x] 1.4 Lint passes (`uv run ruff check .`) — 176ff75
- [x] 1.5 Import-linter contract:1 passes (`uv run lint-imports`) — 176ff75

#### Manual

- [x] 1.6 `get_user_model()` prints the identity User — 176ff75
- [x] 1.7 `showmigrations identity` shows 0001_initial applied — 176ff75

### Phase 2: Configuration & Hardening

#### Automated

- [x] 2.1 `uv run python src/manage.py check` passes (dev, env unset) — fe82ff3
- [x] 2.2 `E001` fires (DEBUG=False + DEV_AUTH_BYPASS_SUB set) — fe82ff3
- [x] 2.3 `W001` fires (DEBUG=False + empty OWNER_SUB_ID) — fe82ff3
- [x] 2.4 `uv run ruff check .` passes — fe82ff3

#### Manual

- [x] 2.5 `check --deploy` runs the new checks (not silently no-op'd) — fe82ff3
- [x] 2.6 `.env.example` documents every new var — fe82ff3

### Phase 3: BFF OAuth + RBAC Plumbing

#### Automated

- [x] 3.1 System test passes (`uv run pytest tests/system/test_auth_flow.py`) — fc308ac
- [x] 3.2 System check passes (`uv run python src/manage.py check`) — fc308ac
- [x] 3.3 Import-linter passes (contract:1 + contract:2) — fc308ac
- [x] 3.4 `uv run ruff check .` passes — fc308ac

#### Manual

- [x] 3.5 `curl /api/me` returns 401 unauthenticated — fc308ac
- [x] 3.6 `/api/users` returns 403 as User, 200 as Owner (via bypass) — fc308ac

### Phase 4: Dev Experience

#### Automated

- [x] 4.1 System check passes (`uv run python src/manage.py check`)
- [x] 4.2 Existing tests still pass (`uv run pytest`)
- [x] 4.3 `uv run ruff check .` passes

#### Manual

- [x] 4.4 Bypass auto-authenticates (curl /api/me → 200 with DEV_AUTH_BYPASS_SUB set)
- [x] 4.5 Django admin login as seeded dev admin; identity_user visible, role/is_owner read-only

### Phase 5: Templates (Welcome / Login / Main)

#### Automated

- [ ] 5.1 System check passes (`uv run python src/manage.py check`)
- [ ] 5.2 `uv run ruff check .` passes
- [ ] 5.3 Existing tests still pass (`uv run pytest`)

#### Manual

- [ ] 5.4 `/` unauthenticated → welcome with Login button
- [ ] 5.5 Login → main page shows "logged in as {nick} ({role})"
- [ ] 5.6 Logout → returns to welcome

### Phase 6: Test Infrastructure (DEV/UAT Scaffolding)

#### Automated

- [ ] 6.1 `uv run pytest` runs DEV suite, skips uat
- [ ] 6.2 `uv run pytest -m uat` skips without RUN_UAT=1
- [ ] 6.3 `uv run ruff check .` passes
- [ ] 6.4 `uv run lint-imports` passes

#### Manual

- [ ] 6.5 `.env.uat` gitignored
- [ ] 6.6 `.github/workflows/uat.yml` is valid YAML

<!-- Phase 7 (Docker Dev Environment) deferred by plan-review F6 — not tracked here. See the "Deferred: Docker Dev Environment" section above. -->
