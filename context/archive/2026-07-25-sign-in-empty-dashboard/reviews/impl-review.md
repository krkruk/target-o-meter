<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Sign-in + empty dashboard (S-01)

- **Plan**: `context/changes/sign-in-empty-dashboard/plan.md`
- **Scope**: All phases (1–5)
- **Date**: 2026-07-26
- **Verdict**: NEEDS ATTENTION
- **Findings**: [0 critical] [2 warnings] [3 observations]

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS (23/23 items match intent) |
| Scope Discipline | PASS (Phase 5 addendum documented in-plan) |
| Safety & Quality | WARNING (2 findings) |
| Architecture | PASS (import-linter contracts KEPT) |
| Pattern Consistency | WARNING (1 finding) |
| Success Criteria | PASS (make check + 104 pytest + 29 vitest + tsc green) |

## Success criteria — verified this session

| Check | Result |
|---|---|
| `make check` (ruff + import-linter + tsc, be+fe) | PASS — both contracts KEPT, all lint clean |
| `uv run pytest` | PASS — 104 passed |
| `npm run test` (vitest) | PASS — 29 passed (5 files) |
| `npx tsc --noEmit` | PASS — clean |
| Manual items (1.10, 2.5, 3.10, 4.1, 4.2, 5.10, 5.12) | marked `[x]` with commit SHAs; Phase 5 manual rows backed by the dotenv/proxy/pipeline code in ae89a16 |

## Plan-drift summary

No material drift across all 23 planned items. Phase 5.2 cleanly reversed Phase 1.4's `/v1/` OIDC prefix back to `/` (NinjaAPI mount at `/v1/` untouched → `/v1/me`, `/v1/users`), pinned by a `/v1/login`→404 regression guard. Two plan-wording nits (not findings — code is correct): the plan wrote `DJANGO_VITE_CFG`/`{% vite %}` but the correct django-vite 3.x names `DJANGO_VITE`/`{% vite_asset %}` were used.

## Findings

### F1 — BFF callback bypasses the service/DTO seam (inline ORM import + 3 redundant queries)

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Pattern Consistency
- **Location**: src/bff/routers/auth_routes.py:95-100, 109
- **Detail**: The callback calls the proper service (`get_or_create_user_by_sub(sub)` → DTO) then immediately discards it and re-imports the model inline to do raw ORM lookups (`User.objects.get(sub=sub)`, `User.objects.exclude(sub=sub).exists()`). Every other BFF router (owner_routes, session_routes, api.py's require_owner) goes through identity/services.py and touches DTOs only — this is the F-01 house style. The inline import reads as hidden from a reader/import-linter. First-login can be derived from `get_or_create`'s `_created` flag, eliminating the two redundant queries.
- **Fix A ⭐ Recommended**: Add a service method returning the row (and the `_created` flag); BFF never imports `User`.
  - Strength: Restores the BFF→service→DTO pattern uniformly; cuts 2 redundant queries per login; first-login falls out of `_created` for free.
  - Tradeoff: Touches identity/services.py + auth_routes.py + possibly the owner-bootstrap test.
  - Confidence: HIGH — `get_or_create_user_by_sub` is the obvious home.
  - Blind spot: Haven't confirmed whether the owner-bootstrap test pins the exact ORM query shape.
- **Fix B**: Leave as-is, document the deviation in-plan as an addendum.
  - Strength: Zero code change; preserves the Phase 5.8 logging test exactly as committed.
  - Tradeoff: The pattern deviation persists and becomes the precedent for the next BFF route to inline ORM.
  - Confidence: MED — depends on whether reviewers treat this as load-bearing pattern.
  - Blind spot: Future BFF routes may copy the inline-import shape.
- **Decision**: FIXED via Fix A (2026-07-26). Added `get_or_create_user_row(sub) -> tuple[User, bool]` to `src/domains/identity/services.py` (returns ORM row + `is_first_login_ever` derived from `not User.objects.exists()` *before* the upsert — also fixes a latent bug where a returning sole-user would re-fire the WARNING). `get_or_create_user_by_sub` is now a thin DTO wrapper over it. `auth_routes.py` callback dropped the inline `from src.domains.identity.models import User` and the two redundant queries; the owner-bootstrap logging test still passes (mocks the token exchange, not the ORM). 36 tests green (`test_owner_bootstrap_logging` + `test_auth_flow` + `test_services`).

### F2 — No system check for insecure SECRET_KEY fallback or default DEBUG=True

- **Severity**: ⚠️ WARNING
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Safety & Quality
- **Location**: src/target_o_meter/settings.py:47-51, 54
- **Detail**: settings.py ships a hardcoded insecure SECRET_KEY fallback and defaults DEBUG=True. checks.py registers E001 (dev_auth_bypass in prod) and W001 (owner sub empty) but NO check for the insecure key or DEBUG=True in prod. A prod deploy that forgets AUTH0_SECRET/SECRET_KEY signs session cookies + CSRF tokens with a publicly-known repo key → session forgery. Forgetting DEBUG=False leaks full stack traces + static file listings.
- **Fix A ⭐ Recommended**: Add E002 (SECRET_KEY startswith "django-insecure-" while DEBUG=False → refuse boot) + W002 (DEBUG=True in prod-shaped config → warn).
  - Strength: Closes the worst-case (session forgery) at boot, where E001 already proves the pattern works.
  - Tradeoff: Two new checks + tests; the SECRET_KEY predicate needs care (only flag the literal fallback).
  - Confidence: HIGH — E001/W001 are the exact template.
  - Blind spot: Whether Render's deploy already sets DEBUG/SECRET_KEY via the deploy-plan (if so, defense-in-depth).
- **Fix B**: Drop the insecure fallback entirely (no default → boot fails loudly if env var missing).
  - Strength: Simplest fail-closed; no predicate to maintain.
  - Tradeoff: Breaks "clone-and-run" dev ergonomics — every fresh checkout needs a SECRET_KEY in .env.
  - Confidence: MED — dev ergonomics matter for onboarding.
  - Blind spot: Haven't checked whether .env.example ships a dev key.
- **Decision**: FIXED via Fix A (2026-07-26). Added E002 (`SECRET_KEY` resolves to the `django-insecure-…` fallback while `DEBUG=False` → refuse boot) and W002 (`DEBUG=True` while `APP_BASE_URL` is a non-localhost host → warn) to `src/target_o_meter/checks.py`, matching the E001/W001 style (`@register(Tags.security)`, plain Error/Warning so they fire on every `manage.py` command). Predicate is the `django-insecure-` prefix (rotating the literal default won't silently disable the check); W002 reads `APP_BASE_URL` from `os.environ` to match `settings._allowed_hosts` resolution. Added 6 tests (E002 fires/inert-with-real-key/inert-in-dev; W002 fires/inert-unset/inert-localhost). Full suite: 110 passed (was 104); `make check` green.

### F3 — load_dotenv() return value silently ignored

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Reliability
- **Location**: src/target_o_meter/settings.py:28-29
- **Detail**: `load_dotenv(_env_file) if _env_file else load_dotenv()` discards the bool return. A missing/unreadable .env (or a wrong TOM_ENV_FILE path) silently fails open with empty-string env defaults — producing the confusing hostless-discovery-URL crash with no "could not load .env" signal. Prod is fine (platform injects env directly); the risk is dev/TOM_ENV_FILE misconfiguration.
- **Fix**: Capture the return; `logger.warning("TOM_ENV_FILE=%s not found / unreadable", _env_file)` on False. One-line change.
- **Decision**: FIXED (2026-07-26). Captured `load_dotenv()`'s return into `_env_loaded` and emit a `target_o_meter.settings` WARNING when `TOM_ENV_FILE` was explicitly set but the file failed to load. Default no-arg `load_dotenv()` is intentionally silent (the prod no-.env case). `test_env_loading.py` + `test_system_checks.py` still green; `make check` clean.

### F4 — ViteProxy forwards the Cookie header (incl. sessionid) to the Vite dev server

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/bff/dev_vite_proxy.py:80-84
- **Detail**: `_proxy_to_vite` forwards every request header except hop-by-hop and a small `_CLIENT_ONLY` set. `Cookie` is in neither, so sessionid + csrftoken get forwarded to http://localhost:5173. Vite ignores them (no exploit), and the whole path is dev-only (custom runserver gated on `DEBUG or insecure_serving`, never used by gunicorn). Defense-in-depth only.
- **Fix**: Add `"cookie"` to `_CLIENT_ONLY` (or a new `_browser_session` set).
- **Decision**: FIXED (2026-07-26). Added `"cookie"` to `_CLIENT_ONLY` in `src/bff/dev_vite_proxy.py` so `sessionid`/`csrftoken` aren't forwarded to the Vite dev server. `test_vite_dev_server.py` still green.

### F5 — No try/except around Auth0 token exchange; failures surface as raw 500

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Reliability
- **Location**: src/bff/routers/auth_routes.py:87
- **Detail**: `oauth.auth0.authorize_access_token(request)` raises Authlib exceptions (MismatchingStateError, CSSError, expired-token) on tampered/replayed/expired callbacks. No handler → Django 500. Fail-closed is correct (no session created, no row mutated) and DEBUG=False hides the traceback. The cost is UX: an expired callback shows a generic server error instead of "login expired, try again".
- **Fix**: Wrap in try/except AuthlibError → redirect to `reverse("bff:login")` (or a 400 with a retry link). Not blocking.
- **Decision**: FIXED (2026-07-26). Wrapped `oauth.auth0.authorize_access_token` in `try/except (OAuth2Error, JoseError)` in `src/bff/routers/auth_routes.py`; on failure logs a WARNING and returns a 400 with a `<a href="/login">` retry link (no redirect — a stale `state` could loop). Fail-closed preserved (no session created, no row mutated). New test `test_callback_returns_400_on_token_exchange_failure` pins the 400, the retry link, and the no-row invariant. Full suite: 111 passed (was 104 pre-triage); `make check` green.

## Items verified clean (no findings)

Migration 0002 (schema-only, safe), cross-domain isolation (import-linter independence + BFF-above-domains both KEPT), no HTTP in domains, no open-redirect on login/callback (`url_has_allowed_host_and_scheme` + server-side session nonce), logout `returnTo` is non-user-input, POST logout + PATCH /v1/me CSRF both implemented and pinned by live-stack tests, Zero Email Storage intact (`sub` never reaches the client; only the opaque OIDC subject is logged, and only on first login), `dev_auth_bypass` two-layer DEBUG gate, `SameSite=Lax` preserved, `set_nick` race backed by the CI-uniqueness constraint, no `dangerouslySetInnerHTML`, runserver.py override does not activate in prod.
