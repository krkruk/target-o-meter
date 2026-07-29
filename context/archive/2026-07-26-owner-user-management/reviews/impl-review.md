<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Owner User Management (S-04)

- **Plan**: context/changes/owner-user-management/plan.md
- **Scope**: All 4 phases (full plan review)
- **Date**: 2026-07-29
- **Verdict**: APPROVED (after triage — all findings resolved)
- **Findings**: 0 critical | 2 warnings | 5 observations

## Verification gate (all green)

- `make check` ✓ — ruff, tsc, import-linter (Domain Isolation KEPT, BFF Above Domains KEPT)
- `make be-test` ✓ — 255 passed
- `make fe-test` ✓ — 86 passed (12 files)

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS (18/20 items MATCH; 2 minor sub-feature drifts) |
| Scope Discipline | PASS (dropped features honored; EXTRA in-scope/beneficial) |
| Safety & Quality | WARNING (2 findings — real correctness bugs) |
| Architecture | PASS (import contracts KEPT; DTO boundary clean) |
| Pattern Consistency | WARNING (3 findings — minor hygiene) |
| Success Criteria | PASS (make check ✓, be-test 255 ✓, fe-test 86 ✓) |

## Security invariants verified clean (no findings)

Owner self-ban/delete guard at both service + route layers; ban check is before `login()` and fail-closed; `banned.html` auto-escapes `reason` (no stored XSS); every owner route behind `require_owner` + `session_auth`; CSRF enforced (403-without-CSRF tests pass); no N+1 in `list_users_for_owner`; no ORM in the router; callback receives a DTO not an ORM object.

## Findings

### F1 — Overlapping-active-ban create/lift asymmetry (Unban looks broken)

- **Severity**: ⚠️ WARNING
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Safety & Quality
- **Location**: src/domains/identity/services.py:225-270
- **Detail**: `ban_user` has no check for an existing active ban and `Ban.Meta` has no constraint, so two bans can be created against one user (e.g. 1h, then 7d before the first expires). `unban_user` lifts only the latest-`banned_until` row; the other stays active → after "Unban", `get_active_ban` still returns a ban → `is_banned` stays True. The owner's Unban appears to do nothing. The UI hides the Ban button while `is_banned` (mitigating the normal path), but the API has no guard.
- **Fix A ⭐ Recommended**: Reject `ban_user` when an active ban already exists — add an active-ban check at the top raising a typed `ActiveBanExistsError` → BFF maps to 409 "User is already banned".
  - Strength: Smaller, clearer; enforces a "one active ban per user" model matching the UI's mental model (no Ban button while banned).
  - Tradeoff: Loses the plan's 4.2 "Extend ban" affordance — but that sub-feature was never implemented (F3), so no regression.
  - Confidence: HIGH — mirrors the NickTakenError/NoActiveBanError pattern already in this file.
  - Blind spot: A future "extend ban" feature would lift-then-create atomically rather than allow two active rows.
- **Fix B**: Make `unban_user` lift ALL active bans (`Ban.objects.filter(user=..., active...).update(lifted_at=now())`).
  - Strength: Keeps `ban_user` permissive; one Unban reliably clears state.
  - Tradeoff: Allows an ambiguous "two active bans" state between create and lift; `get_active_ban`'s "latest" tie-break becomes load-bearing.
  - Confidence: MED — works but leaves a shape the UI never intends to produce.
  - Blind spot: `get_ban_status`'s active query must agree with unban's filter for the chip to update correctly.
- **Decision**: FIXED (Fix A) — `ActiveBanExistsError` added; `ban_user` rejects a second active ban; BFF maps to 409 "User is already banned". Covered by 2 new unit tests (`test_ban_user_refuses_overlapping_active_ban`, `test_ban_user_allows_reban_after_lift`) and 1 system test (`test_ban_409_on_already_banned`).

### F2 — total_pages miscomputed after a delete (pager vanishes, page 2 unreachable)

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/frontend/src/components/AdminUsersPage.tsx:83-94
- **Detail**: `handleDeleted` derives `total_pages` from the page-local `items.length`, not the global total: `Math.ceil(items.length / prev.page_size)`. With total=25/page_size=20 (2 pages), deleting one row on page 1 → items.length=19 → total_pages=1 → pager disappears and page 2 is unreachable until a refetch. `total` is decremented correctly; only `total_pages` is wrong.
- **Fix**: Derive `total_pages` from the decremented global total:
  ```ts
  const total = Math.max(0, prev.total - 1);
  total_pages: Math.max(1, Math.ceil(total / prev.page_size)),
  ```
- **Decision**: FIXED — `handleDeleted` now derives `total_pages` from the decremented global `total`. Regression test `keeps total_pages from the global total after a delete` added to `AdminUsersPage.test.tsx`.

### F3 — BanModal "extend mode" + "View ban" button not implemented (plan drift)

- **Severity**: OBSERVATION
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Plan Adherence
- **Location**: src/frontend/src/components/BanModal.tsx; AdminUsersPage.tsx:192-200
- **Detail**: Plan 4.2/4.4 specified an active-ban row show "Unban + View ban", with View opening BanModal in "extend mode" (pre-fill duration, show current reason/expiry, action becomes "Extend ban" creating a new Ban row). Implementation shows only "Unban" on active-ban rows; BanModal is create-only. The core ban/unban/delete flow is fully present and tested — only this convenience sub-feature is absent. (If F1 Fix A is taken, "extend" semantics disappear anyway, making this moot.)
- **Fix**: Accept as documented scope reduction — add a one-line note to the plan's Progress (4.2/4.4) that extend/View-ban was dropped, OR implement it (open BanModal with the current ban pre-filled; on submit, lift-then-create atomically if F1 Fix A is also taken).
- **Decision**: DOCUMENTED — scope-reduction notes added to plan Progress 4.2 and 4.4. With F1's one-active-ban rule, "extend" semantics can't exist without lifting first, so the drop is the honest record.

### F4 — Esc-to-dismiss documented but not implemented on both modals

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/frontend/src/components/BanModal.tsx:49-56; DeleteUserModal.tsx:38-45
- **Detail**: Both file headers state "Dismissable (Esc / overlay click / Cancel)" but there is no keydown handler, so Esc does nothing. Overlay-click and Cancel work. NickPrompt (the comparison) is intentionally non-dismissable.
- **Fix**: Add a `useEffect`/`onKeyDown` Esc handler calling `onClose`, or drop "Esc" from the docstrings.
- **Decision**: FIXED — `useEffect` keydown listener added to both `BanModal` and `DeleteUserModal`; Esc calls `onClose` unless a submit is pending (so it can't interrupt an in-flight request).

### F5 — Orphaned list_users() is now dead code

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/domains/identity/services.py:131-140
- **Detail**: The old demo `list_users()` (returns `list[UserOut]`) is no longer called by any BFF route — `owner_routes.py` uses `list_users_for_owner`. Its docstring ("Backs the demo owner route... until S-04 adds real data") is now stale; S-04 has landed. It survives only because `test_services.py` still tests it.
- **Fix**: Delete `list_users` and its test (cleaner), or update the docstring to state explicitly why it's retained.
- **Decision**: FIXED — `list_users()` deleted from `services.py`, its test removed from `test_services.py`, the now-unused `UserOut` import dropped, and `README.md` references updated to `list_users_for_owner`.

### F6 — get_active_ban docstring overstates its boundary role

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/domains/identity/services.py:186-204
- **Detail**: Docstring claims `get_active_ban` is "the single ORM-returning read accessor — used by the OAuth callback's enforcement check" and mirrors `get_or_create_user_row`'s exception. But the callback actually calls `get_ban_status` (DTO). `get_active_ban` is used only internally by `unban_user` (and tests) — it does not cross the boundary. The framing muddies the §5 "DTOs only across boundaries" story by implying a second ORM-returning exception that doesn't exist.
- **Fix**: Rename to `_get_active_ban` (private) and fix the docstring to say it backs `unban_user` — leaving `get_or_create_user_row` as the sole documented ORM-returning boundary exception, as the plan intended.
- **Decision**: FIXED — renamed to `_get_active_ban`; docstring + the S-04 section comment rewritten to state it backs `ban_user`/`unban_user` internally and does NOT cross the boundary; all call sites + tests updated.

### F7 — Hardcoded danger/error palette bypasses design tokens

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: AdminUsersPage.module.css; BanModal.module.css; DeleteUserModal.module.css
- **Detail**: Neutral colors correctly use `var(--color-border)`/`--color-muted`, but the red/grey ban-chip and error palette is hardcoded (`#b91c1c`, `#fee2e2`, `#fecaca`, `#6b7280`, `#b00020`). NickPrompt (the comparison) uses tokens consistently. This fragments the danger palette across files.
- **Fix**: Add `--color-danger` / `--color-danger-bg` / `--color-warning` tokens to `styles.css` alongside `--color-primary` and use them for chips, the delete button, and error text.
- **Decision**: FIXED — 9 semantic tokens added to `styles.css` (`--color-danger`, `--color-danger-strong`, `--color-danger-bg`, `--color-danger-border`, `--color-warning-bg`, `--color-warning-border`, `--color-warning-text`, `--color-neutral-bg`, `--color-neutral-border`); all three CSS modules routed through them. `#b00020` consolidated into `--color-danger` (#b91c1c).
