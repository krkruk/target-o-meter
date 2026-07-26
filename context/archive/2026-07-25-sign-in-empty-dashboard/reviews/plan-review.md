<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Sign-in + empty dashboard (S-01)

- **Plan**: `context/changes/sign-in-empty-dashboard/plan.md`
- **Mode**: Deep
- **Date**: 2026-07-25
- **Verdict**: SOUND (after triage — was REVISE)
- **Findings**: 1 critical · 5 warnings · 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | WARNING (F2) |
| Blind Spots | WARNING (F3, F4, F5) |
| Plan Completeness | WARNING (F1, F6) + 1 observation (F7) |

## Grounding

17/17 paths ✓, 6/6 symbols ✓, brief↔plan ✓

## Findings

### F1 — Backfill predicate contradicts the dev-admin's actual nick shape

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Plan Completeness
- **Location**: Phase 1.1 — `has_set_nick` field + migration
- **Detail**: Plan §1.1 backfilled `has_set_nick=True` for non-`shooter-*` nicks "so the seeded dev-admin isn't prompted", but `create_superuser` → `create_user` → `_generated_nick()` produces a `shooter-<uuid8>` nick, so the predicate leaves the dev-admin at `False`. Also no `RunPython` seeds users, so the backfill is a no-op on any real DB.
- **Fix A ⭐ Applied**: Drop the backfill; ship `0002` schema-only with `default=False`.
  - Strength: Honest about empty-DB reality; removes self-contradicting predicate; new OAuth users prompt on first login (desired UX).
  - Tradeoff: A `createsuperuser` dev-admin is prompted once.
  - Confidence: HIGH — verified no seeded rows exist.
  - Blind spot: Whether anyone has a long-lived local dev DB with real OAuth rows (unlikely pre-Phase-4).
- **Decision**: FIXED via Fix A (2026-07-25)

### F2 — POST logout recommendation contradicts the actual code shape

- **Severity**: ⚠️ WARNING
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Architectural Fitness
- **Location**: Phase 1.5 — Logout GET → POST + CSRF
- **Detail**: Plan §1.5 said "pick the ninja-router path for consistency with session_routes". But `logout` is a plain Django view (`auth_routes.py:103-117`) registered top-level in `urls.py:29`, not on a ninja Router. `login_view` and `callback` are identical in shape; the `router = Router()` at `auth_routes.py:37` is dead code. Moving only `logout` to a ninja Router while leaving the other two as Django views would split the OIDC chain across two styles.
- **Fix Applied**: Keep `logout` as a plain Django view; decorate with `@require_POST` + `@csrf_protect`; URL rename to `v1/logout`. Optionally delete the dead `router = Router()`.
  - Strength: Matches the actual OIDC-chain shape; minimal churn; `@csrf_protect` is the Django-native CSRF path for plain views.
  - Tradeoff: Two CSRF enforcement mechanisms in the codebase (ninja-auto for PATCH, decorator for POST logout).
  - Confidence: HIGH.
  - Blind spot: SPA CSRF token flow (see F3, F5).
- **Decision**: FIXED (2026-07-25)

### F3 — CSRF auto-enforcement is asserted but unverified in-repo

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 1.3 — `PATCH /v1/me`; "Critical Implementation Details"
- **Detail**: Plan asserted "django-ninja auto-enforces CSRF for non-GET under SessionAuth — verify with a test", but the contract was grounded only in the `bff/api.py:3-6` docstring. No non-GET endpoint exists in the repo today, and Phase 1.7 had happy/409/401 PATCH tests but no CSRF test.
- **Fix Applied**: Add explicit CSRF tests to Phase 1.7 — PATCH /v1/me and POST /v1/logout return 403/401 without `X-CSRFToken`, 200 with a valid token sourced from the `csrftoken` cookie.
  - Strength: Converts a docstring assertion into a tested invariant; locks the contract the SPA logout/nick flow depends on.
  - Tradeoff: One more test per endpoint.
  - Confidence: HIGH.
  - Blind spot: Exact status code (403 vs ninja auth-ordering) — the test pins it down.
- **Decision**: FIXED (2026-07-25)

### F4 — Rename blast radius misses `test_dev_bypass.py`

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 1.7 — Backend tests for the rename
- **Detail**: The rename breaks `tests/system/test_dev_bypass.py:43,63` (two `client.get("/api/me")` calls) which the plan did not enumerate.
- **Fix Applied**: Added `test_dev_bypass.py` to Phase 1.7 (`/api/me` → `/v1/me`); added a one-line grep sweep note to Migration Notes (`/api/` and `/bff/` literals repo-wide).
- **Decision**: FIXED (2026-07-25)

### F5 — SPA CSRF token source is unspecified

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 2.2 (`base.html`) + Phase 3.1 (`api.ts`)
- **Detail**: The SPA's PATCH /v1/me and POST /v1/logout read the `csrftoken` cookie, but Django only sets it when `get_token()` runs during the request. Phase 2.2 base.html had no `{% csrf_token %}`, so an unauthed first load may never set the cookie before the first PATCH/POST.
- **Fix Applied**: Add `{% csrf_token %}` to base.html — Django sets the cookie on every `/` render, including unauthed first load.
  - Strength: One-line change; works for both authed and unauthed first-load; standard Django SPA pattern.
  - Tradeoff: Sets a cookie on unauthed responses (already the status quo for any CSRF-middleware site).
  - Confidence: HIGH.
  - Blind spot: None significant.
- **Decision**: FIXED (2026-07-25)

### F6 — `_user_to_context_dto()` construction site not called out

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 1.2 — `set_nick` service; Phase 1.3 — `PATCH /v1/me`
- **Detail**: Adding a non-optional `has_set_nick: bool` to `UserContextDTO` breaks its one construction site: `_user_to_context_dto()` in `services.py:16-24`. Plan §1.2 didn't note that `get_user_context` → `_user_to_context_dto` must populate the new field. Pydantic raises at runtime if missed.
- **Fix Applied**: Added explicit bullet to §1.2 — update `_user_to_context_dto` to populate `has_set_nick=user.has_set_nick`.
- **Decision**: FIXED (2026-07-25)

### F7 — Two Phase-1 success criteria lacked matching Progress items

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 1 — Progress ↔ Success Criteria mapping
- **Detail**: Phase 1's Success Criteria included "makemigrations produces clean 0002" and "migrate applies cleanly on a fresh DB", but no Progress item (1.1–1.10) corresponded to running either command.
- **Fix Applied**: Repurposed Progress item 1.8 to run `makemigrations identity` (verify `0002` file shape) + `migrate`; shifted downstream items to 1.9+.
- **Decision**: FIXED (2026-07-25)
