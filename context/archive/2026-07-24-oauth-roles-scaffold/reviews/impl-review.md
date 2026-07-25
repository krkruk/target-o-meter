<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: OAuth + Roles Scaffold (F-01)

- **Plan**: context/changes/oauth-roles-scaffold/plan.md
- **Scope**: Phases 1–6 of 6
- **Date**: 2026-07-25
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical · 6 warnings · 5 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | WARNING (1 finding — F5) |
| Safety & Quality | WARNING (3 findings — F1, F2, F4, F6) |
| Architecture | PASS |
| Pattern Consistency | PASS (1 minor — F3) |
| Success Criteria | PASS |

## Live verification (re-run during review)

- `manage.py check` → 0 issues
- `ruff check .` → All checks passed
- `lint-imports` → 2 contracts KEPT (domain isolation + BFF above domains)
- `pytest` → 65 passed, 0 failed
- `pytest -m uat` → 65 deselected (no UAT test exists yet — Q7 deferred)
- E001 fires under DEBUG=false + DEV_AUTH_BYPASS_SUB=… ✓
- W001 fires under DEBUG=false + empty OWNER_SUB_ID ✓

## Findings

### F1 — _safe_next accepts http:// same-host URLs behind Render's TLS terminator

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/bff/routers/auth_routes.py:53 (settings.py lacked SECURE_PROXY_SSL_HEADER)
- **Detail**: `_safe_next` passes `require_https=request.is_secure()`. Behind Render's TLS-terminating proxy, `is_secure()` returns False unless `SECURE_PROXY_SSL_HEADER` is set — and it isn't. So in prod an http:// URL pointing at the same host passes the check. Verified `url_has_allowed_host_and_scheme` REJECTS off-site hosts regardless of `require_https`, so this is NOT a credential-stealing off-site redirect (initial safety report overstated it). Actual exposure: same-host TLS-downgrade acceptance on the post-login redirect.
- **Fix**: Set `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` in settings.py, gated on SECURE_COOKIES so dev is unaffected.
- **Decision**: FIXED — added `SECURE_PROXY_SSL_HEADER` block gated on `SECURE_COOKIES` at settings.py:154-167. Verified dev suite (20 system tests) still passes; system check clean.

### F2 — get_user_context can raise DoesNotExist → 500 on /api/me and require_owner

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/domains/identity/services.py:48 (callers src/bff/api.py:51, src/bff/routers/session_routes.py:23)
- **Detail**: `get_user_context` does `User.objects.get(sub=sub)` with no try/except. A valid Django session whose `sub` no longer has a row (row deleted in S-04, or `sub` drift after an Auth0 tenant migration) raises `User.DoesNotExist` → unhandled 500 on an authenticated request. The `/api/me` docstring promises 401; `require_owner` promises 403. Both return 500 in this edge case.
- **Fix**: Catch `User.DoesNotExist` in both BFF callers and raise `HttpError(401)`.
- **Decision**: FIXED — wrapped both call sites (`src/bff/api.py:require_owner`, `src/bff/routers/session_routes.py:me`) in try/except mapping `User.DoesNotExist` → `HttpError(401, "Session user no longer exists")`. Added two regression tests in `tests/system/test_auth_flow.py` (`test_api_me_returns_401_when_session_user_row_gone`, `test_api_users_returns_401_when_session_user_row_gone`) that delete the row mid-session and assert 401. All 9 auth_flow tests pass.

### F3 — make_owner writes os.environ directly, bypassing monkeypatch

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/domains/identity/test_utils.py:32
- **Detail**: `make_owner` does `os.environ["OWNER_SUB_ID"] = sub` directly. The docstring tells callers to use `monkeypatch.setenv`, and both conftests do — but the function itself bypasses monkeypatch and mutates the real process env. A future test that calls `make_owner` without the fixture leaks `OWNER_SUB_ID` into sibling tests. Vision's `test_utils.py` is a pure row factory with no env mutation; this diverges from the pattern.
- **Fix**: Remove the `os.environ` line from `make_owner`. Make it a pure row factory; rely on the `owner_sub` fixture (already monkeypatches) to set `OWNER_SUB_ID`.
- **Decision**: FIXED — removed the `os.environ["OWNER_SUB_ID"] = sub` line from `make_owner` (and the now-unused `import os`); updated the docstring to make explicit that the seeder is a pure row factory and the caller owns the env via the `owner_sub` fixture. Full suite (67 tests) still passes.

### F4 — admin exposes is_staff/is_superuser as editable fields

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/domains/identity/admin.py:45
- **Detail**: The `fieldsets` include `("Permissions", {"fields": ("is_staff", "is_superuser", "groups", "user_permissions")})`. The docstring calls the admin "read-mostly", but `is_staff` and `is_superuser` are editable. A staff user can promote themselves to `is_superuser` → full Django admin bypass. For F-01 (single seeded dev admin only) this is acceptable; flagging because the docstring undersells it.
- **Fix**: Add `is_staff`, `is_superuser`, `groups`, `user_permissions` to `readonly_fields`.
- **Decision**: FIXED — added the four permission fields to `readonly_fields` in `src/domains/identity/admin.py`; relabeled the Permissions fieldset to "(read-only — promote via shell)" and updated the module docstring to make the read-mostly stance explicit. System check clean; `test_identity_user_is_registered_in_admin` still passes.

### F5 — src/domains/identity/README.md is unplanned doc surface

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Scope Discipline
- **Location**: src/domains/identity/README.md (commit fa157a1)
- **Detail**: A 230-line onboarding README for the identity domain was added in a standalone commit after the plan was closed out. Not named in the plan. No other domain has a README (src/domains/vision/ has none). The content is accurate (spot-checked), but it's net-new doc surface that will drift from code if not maintained, and it sets a precedent (does vision/core need parity?).
- **Decision**: DISMISSED — user noted vision already has a README (`src/domains/vision/README.md`, 248 lines). Verified: the identity README (230 lines) follows the established pattern rather than breaking it, so the parity concern in the original finding was based on a false premise ("no other domain has a README"). The README is a useful onboarding doc for future developers; no action taken.

### F6 — create_superuser signature diverges from Django's positional contract

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/domains/identity/models.py:66-79
- **Detail**: `create_superuser` pops `password` from extra kwargs with no default. A caller invoking it without `password=` gets a bare `KeyError`, not a clear `TypeError`. Django's contract everywhere else passes `password` positionally. Also: settings.py defines `DEV_ADMIN_SUB` / `DEV_ADMIN_NICK` / `DEV_ADMIN_PASSWORD` (lines 61-63) but no code consumes them — the Docker dev-seed (Phase 7) was deferred, so these are dead config implying a feature that doesn't exist yet.
- **Fix**: Make the signature `create_superuser(self, sub, nick="", password="", **extra)` to match Django's expected shape; raise a clear `ValueError` on empty password.
- **Decision**: FIXED — promoted `password` to a named param in `src/domains/identity/models.py:create_superuser` with an explicit empty-password `ValueError`. Left `DEV_ADMIN_*` env vars in place — they're documented as deferred to the Docker phase (Phase 7), not dead config; the Docker change will wire them. Identity tests (7) still pass; system check clean.

## Observations (no action required, recorded for context)

- **O1** — `require_owner` moved from `auth=[...]` list to body call (src/bff/routers/owner_routes.py:28,36). 401/403 contract preserved (verified). The `api.py` docstring's auth-list→500 rationale is partially overstated; consider correcting.
- **O2** — `UserAdmin` extends Django's `UserAdmin`, not bare `ModelAdmin` (src/domains/identity/admin.py:21). Behavior matches intent; arguably more correct.
- **O3** — roadmap.md edit touched two lines, not one (context/foundation/roadmap.md:32,64). Both edits are the same email→sub correction and are correct.
- **O4** — Three extra system tests (`test_dev_bypass`, `test_system_checks`, `test_templates`) not named in plan §3.7. They automate Phase 2/4/5 manual checks as regression tests. Recommend amending plan success criteria or noting in change.md.
- **O5** — CSRF cookie `HttpOnly=False` widens XSS→CSRF surface (src/target_o_meter/settings.py:154). Documented intent for the future SPA (S-01). Acceptable for F-01; revisit when React lands.
