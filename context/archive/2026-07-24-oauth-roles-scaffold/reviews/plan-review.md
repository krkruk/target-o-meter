<!-- PLAN-REVIEW-REPORT -->
# Plan Review: OAuth + Roles Scaffold (F-01)

- **Plan**: `context/changes/oauth-roles-scaffold/plan.md`
- **Mode**: Deep
- **Date**: 2026-07-24
- **Verdict**: SOUND (post-triage; was REVISE)
- **Findings**: 2 critical · 2 warnings · 2 observations — all 6 resolved (4 FIXED, F5 noted for S-01, F6 Docker deferred)

## Verdicts

Post-triage dimension verdicts (all FAILs/WARNINGs resolved by applied fixes):

| Dimension | Verdict (pre → post) |
|-----------|---------|
| End-State Alignment | WARNING → PASS |
| Lean Execution | WARNING → PASS |
| Architectural Fitness | FAIL → PASS |
| Blind Spots | FAIL → PASS |
| Plan Completeness | WARNING → PASS |

## Grounding

paths ✓ (identity stubs, bff/frontend empty, tests dirs empty, vision reference, no Docker/root-conftest all confirmed), symbols ✓ (AUTH_USER_MODEL absent, TEMPLATES DIRS=[], .importlinter contract:1=independence, authlib absent, django-ninja present), brief↔plan ✓. Progress↔Phase mechanical contract ✓.

The approach is sound and well-grounded; the two FAIL dimensions are localized, fixable defects (not a wrong approach). Honest call: REVISE.

## Findings

### F1 — User model omits PermissionsMixin but Phase 4.2 needs has_perm/has_module_perms

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Architectural Fitness
- **Location**: Phase 1.1 (User model) vs Phase 4.2 (Django admin)
- **Detail**: Phase 1.1 specifies `User(AbstractBaseUser)` with NO PermissionsMixin. Phase 4.2 registers User in a ModelAdmin and wants the dev admin to SEE `identity_user` rows. Verified against Django 6.0.5: `AdminSite.has_permission` (sites.py:203) only needs is_active+is_staff → admin INDEX loads. But `ModelAdmin.has_view_permission`/`has_module_permission` (options.py:610-631) call `user.has_perm(...)`/`user.has_module_perms(...)`, which exist ONLY on PermissionsMixin (auth/models.py:384,426) — `AbstractBaseUser` does NOT define them. Result: `identity_user` either raises AttributeError or silently disappears. Success criterion 4.5 cannot pass as designed.
- **Fix A ⭐ Recommended**: Add PermissionsMixin to the User model
  - Strength: Unlocks standard Django admin RBAC; zero impact on derived `role`/`is_owner` properties.
  - Tradeoff: Pulls in Django permissions framework (groups/permissions tables) the plan framed as "minimal".
  - Confidence: HIGH — documented Django path for custom users integrating with admin.
  - Blind spot: Adds 2 tables to 0001_initial; migration stays clean-slate.
- **Fix B**: Keep AbstractBaseUser; set is_superuser=True on the dev admin seed
  - Strength: Preserves minimal model; superusers bypass has_perm via ModelBackend (backends.py:115,132).
  - Tradeoff: Superuser dev admin can edit ANY model, not just identity_user; non-superuser staff still broken.
  - Confidence: HIGH — superuser short-circuit is documented.
  - Blind spot: Future non-superuser staff admins remain broken.
- **Decision**: FIXED via Fix A — added `PermissionsMixin` to User (Phase 1.1 contract + brief decision table). — DevAuthBypassMiddleware has no DEBUG guard; E001 doesn't fire under gunicorn

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 2.4 (E001) + Phase 4.1 (middleware) vs Desired End State #6
- **Detail**: Two layered problems: (a) Phase 4.1's middleware contract reads ONLY `DEV_AUTH_BYPASS_SUB` — no `if not settings.DEBUG: return`. Yet plan-brief Open Risks claims "the middleware's own DEBUG gate protects serving-time." Plan body omits that gate → internal contradiction. (b) E001 runs at `manage.py check`/`runserver`, NOT inside the gunicorn/WSGI serving loop. F-01's prod target is Render = gunicorn. Desired End State #6 ("app refuses to start") is NOT guaranteed unless a `manage.py check` release-step runs — and no such gate exists in this change (Phase 6 ships only a UAT CI shell). Net: misconfigured prod env (DEV_AUTH_BYPASS_SUB set + gunicorn) serves with bypass live. Two env vars equal = Owner impersonation.
- **Fix A ⭐ Recommended**: Add `if not settings.DEBUG: return` as the FIRST line of process_request; note CI/release `manage.py check` gate requirement in Migration/Deployment Notes
  - Strength: Closes the hole at the serving layer regardless of launch method; DEBUG gate is correct place (DEBUG=False in prod on Render). Two independent guards.
  - Tradeoff: Slightly redundant with E001 — but that's the point for a prod auth bypass.
  - Confidence: HIGH — DEBUG-gating dev middleware is standard Django practice.
  - Blind spot: Doesn't add a prod CI `manage.py check` gate (out of scope for F-01) — call it out instead.
- **Fix B**: Gate the middleware on a dedicated `DEV_AUTH_BYPASS_ENABLED` flag (default False), assert False when DEBUG=False
  - Strength: Explicit opt-in independent of DEBUG semantics; clearer intent.
  - Tradeoff: One more env var; diverges from "two env vars do everything" minimality.
  - Confidence: MED — cleaner conceptually but more surface area.
  - Blind spot: Render env must never set it — same human-discipline risk.
- **Decision**: FIXED via Fix A — added `if not settings.DEBUG: return` as first line of process_request (Phase 4.1); added release-gate `manage.py check` requirement to Migration Notes; updated brief Open Risks. — import-linter contract:2 (layers) is missing the mandatory `layers=` key

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 3 / Implementation Approach (contract:2 announcement)
- **Detail**: The plan leans on "the new layers contract:2 makes domain→bff a CI failure" but never specifies the contract body. A `type = layers` contract REQUIRES a `layers =` key mapping layer names to package globs (import-linter fails to load without it). Also `src.bff` must be importable for the contract to pass — contract:2 first passes at end of Phase 3.
- **Fix**: Add the full contract block to the plan, e.g.:
  ```
  [importlinter:contract:2]
  name = BFF Above Domains
  type = layers
  layers =
      src.bff
      src.domains.core
      src.domains.identity
      src.domains.vision
  ```
  And state that contract:2 first passes at end of Phase 3 (when src.bff is importable), so Phase 1/2 lint-imports runs only assert contract:1.
- **Decision**: FIXED — added full `[importlinter:contract:2] type=layers layers=...` block + Phase 3 ordering note to Implementation Approach. — /api/me declared `response={..., 401: MeOut}` won't be honored on auth failure

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 3.4 (/api/me endpoint)
- **Detail**: When the `auth` callable fails it raises AuthenticationError → routes through the default exception handler → returns `{"detail": "Unauthorized"}`, NOT a serialized MeOut. Declaring `401: MeOut` is misleading. Desired End State #3 only requires "401 otherwise" (no body shape).
- **Fix**: Either drop `401: MeOut` (declare only `{200: MeOut}`) and document the default 401 body, or register a custom auth-error handler returning `MeOut(authenticated=False, user=None)` if a shaped 401 is wanted for the SPA bootstrap.
- **Decision**: FIXED — dropped `401: MeOut`; Phase 3.4 now declares `response={200: MeOut}` with a note on the default 401 body. — Logout is a GET link → CSRF-soft logout

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 3.3 (logout) + Phase 5.2 (main.html Logout button)
- **Detail**: Phase 5.2 renders Logout as a link → GET `/bff/logout`. GET-triggered logout is a known minor CSRF vector (cross-site GET can log out a user; stale-back-button issues). Not blocking for F-01, but S-01's SPA re-implements this surface.
- **Fix** (optional): Make logout POST with a CSRF token when the UX allows; document the GET choice if kept.
- **Decision**: FIXED — added "POST-based logout" note to "What We're NOT Doing" pointing at S-01. — Phase 7 (Docker) is ~25% of the plan for a "minimal enabler"

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Lean Execution
- **Location**: Phase 7
- **Detail**: change.md frames F-01 as "Minimal enabler only," yet Phase 7 is the single largest phase. The dev-bypass (Phase 4) already enables Auth0-free local dev, so Docker is convenience, not a prerequisite for proving the auth/RBAC contract. Explicitly decided (brief Q round 2); flagging only so the decision is conscious.
- **Fix** (optional): No change if the decision stands; otherwise move Phase 7 to its own change and shrink F-01 to phases 1–6.
- **Decision**: FIXED — Phase 7 (Docker) moved to a "Deferred: Docker Dev Environment" appendix and removed from in-scope/Progress/Testing Strategy; F-01 scope is now Phases 1–6.