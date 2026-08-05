<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: UI Chores

- **Plan**: context/changes/ui-chores/plan.md
- **Scope**: All 5 phases (full plan review) — 797d725^..HEAD (6 commits)
- **Date**: 2026-08-05
- **Verdict**: APPROVED
- **Findings**: 0 critical · 1 warning · 6 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

All five phases implemented faithfully to plan. `make check` green (ruff, both import-linter contracts KEPT, tsc --noEmit). Vitest 132/132 across 20 files, including the four new/extended suites (`TopBar.test.tsx`, `ScoreRow.test.tsx`, `DailyAverageChart.test.tsx`, `Upload.test.tsx`) plus the epilogue Playwright spec (`ui-chores.spec.ts`). Every "Changes Required" item verified MATCH. Four files changed outside any phase's "Changes Required" — all benign and forced by the planned UI (documented below). No XSS vectors, no injection, decorative icons all `aria-hidden`, warning tokens resolve, 760px breakpoint used consistently.

## Findings

### F1 — Static `aria-expanded="false"` is a permanent lie to screen readers

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality (Accessibility)
- **Location**: src/frontend/src/components/TopBar.tsx:22
- **Detail**: The trigger renders `aria-expanded="false"` statically. The menu is stylesheet-driven (`:hover`/`:focus-within`), so the attribute can't reflect reality without React state. This is explicitly sanctioned by the plan (§2.2) and the source comment documents it. Keyboard/touch users can still reach the Logout item via Tab (`:focus-within` reveals it), so the menu stays *operable* — but the SR announcement permanently says "collapsed" even while the menu is open. Acceptable for a single-item menu; would become a real problem if a second menuitem is added.
- **Fix**: Accept as-is for the single-item case (documented, intentional, plan-sanctioned). If accurate SR announcement is wanted, toggle `aria-expanded` via `onFocus`/`onBlur` (or `onMouseEnter`/`onMouseLeave`) handlers on `.nickGroup` — a few lines of state. No change required to ship.
- **Decision**: FIXED — added `useState` for hover + focus on `.nickGroup`, set `aria-expanded={hovered || focused}` so the trigger reflects reality. Extended `TopBar.test.tsx` with an `aria-expanded` focus-toggle test. TopBar vitest 6/6 green.

### F2 — Chart formatters render literal `"NaN"` for `undefined` inputs

- **Severity**: 📋 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality (Reliability)
- **Location**: src/frontend/src/components/DailyAverageChart.tsx:27-33
- **Detail**: `formatChartTick`/`formatChartTooltip` are `Number(v).toFixed(2)`. Verified behavior: `Number(null)` → `"0.00"` (silent coercion), `Number(undefined)` and `Number("abc")` → `"NaN"`. The YAxis `domain={[0,10]}` generates numeric ticks and `dataKey="average"` values come from the API, so the practical risk is low — but the test suite only covers numbers and stringified numbers, never the null/undefined edge case.
- **Fix**: Guard explicitly — `const n = Number(v); return Number.isFinite(n) ? n.toFixed(2) : '';`. Low priority given the bounded domain.
- **Decision**: FIXED — extracted shared `toFixed2` helper with `Number.isFinite` guard, used by both `formatChartTick` and `formatChartTooltip`. Added 2 edge-case tests (undefined, non-numeric string). DailyAverageChart vitest 7/7 green.

### F3 — Stale header comment in Upload.tsx

- **Severity**: 📋 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/frontend/src/components/Upload.tsx:1-3
- **Detail**: The opening header still reads "Upload route — PC file picker. Same flow as Capture minus the `capture` attribute." After Phase 5 the flows are no longer "same minus capture" — Upload now has two custom buttons, hidden inputs, and a PII warning callout that Capture lacks. The Phase 5 block below it correctly documents the new structure; the opening sentence is now misleading and inconsistent with the still-accurate `Capture.tsx:1-5` header.
- **Fix**: Refresh the opening comment to reflect the two-button + warning structure (one or two lines).
- **Decision**: FIXED — rewrote the opening header to describe the two-button + PII warning structure and the /capture fallback relationship, merged with the Phase 5 block.

### F4 — PII warning shown on `/upload` but not on the `/capture` fallback

- **Severity**: 📋 OBSERVATION
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Scope Discipline
- **Location**: src/frontend/src/components/Upload.tsx:62 (vs Capture.tsx — untouched)
- **Detail**: Per the plan's "What We're NOT Doing" section, `/capture` is deliberately kept as a fallback route and was intentionally not touched — so the PII/LLM-training warning that appears on `/upload` does NOT appear on `/capture`. This is a documented decision, not drift, and the code matches the plan. The product implication worth surfacing: a user who lands on `/capture` won't see the "do not upload PII / data is used to train LLM models" disclosure even though the data-handling risk is identical regardless of capture method. Whether that asymmetry is acceptable is a product call, not an implementation defect.
- **Fix A ⭐ Recommended**: Accept the documented asymmetry; if /capture is rarely reached (it's a fallback), surface this as a follow-up note rather than re-opening scope. Add a one-line caveat to the plan's "What We're NOT Doing" section noting the warning's absence on /capture is a known product gap.
  - Strength: Preserves the deliberate scope decision; matches the "fallback, untouched" framing.
  - Tradeoff: The disclosure gap on /capture persists until a future ticket explicitly addresses it.
  - Confidence: HIGH — the plan was explicit about not touching /capture.
  - Blind spot: Haven't checked whether /capture is actually linked anywhere (its reachability determines real exposure).
- **Fix B**: Pull the warning callout into a tiny shared component and render it on both /upload and /capture. (Out of plan scope — would need a follow-up change.)
  - Strength: Eliminates the disclosure asymmetry entirely.
  - Tradeoff: Re-opens the explicitly-closed "/capture untouched" boundary; new change ticket warranted.
  - Confidence: MED — depends on whether the same `PII_WARNING` const wants to live in a shared module.
  - Blind spot: Have not assessed whether /capture is reachable from any current navigation path.
- **Decision**: FIXED via Fix A — accepted the documented asymmetry; added a one-line caveat to `plan.md` §"What We're NOT Doing" noting the warning's absence on `/capture` as a known product gap. No code change to `/capture` (the deliberately-closed boundary stays closed).

- **Severity**: 📋 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: src/frontend/src/components/AppShell.test.tsx, CaliberDistanceStep.test.tsx, tests-acceptance/seed.spec.ts, tests-acceptance/ui-chores.spec.ts
- **Detail**: Four files landed outside the explicit "Changes Required" lists. Each was inspected via `git show`/`git diff`:
  - `AppShell.test.tsx` (p2, a0bdeec) — the existing "fires onLogout" test used a global `getByRole('menuitem', { name: /logout/i })` that became ambiguous once Phase 2 added the TopBar menuitem. Tightened to scope the Sidebar nav. Forced, correct.
  - `CaliberDistanceStep.test.tsx` (p5, d38086a) — the "renders a file input WITHOUT capture" selector matched the new camera input's `aria-label="Capture a photo of your target"`. Narrowed to `/select a photo/i`. Forced, correct.
  - `tests-acceptance/seed.spec.ts` (epilogue, b2bbc2d) — 26-line /10x-e2e convention exemplar; named in the epilogue commit message as documentation, not a planned deliverable.
  - `tests-acceptance/ui-chores.spec.ts` (epilogue, b2bbc2d) — the single Playwright spec the epilogue was created to add; covers all five phases' manual-verification rows.
  All four are mechanical follow-ons or the explicit epilogue deliverable; no production logic touched outside plan intent.
- **Fix**: No action — recording for transparency. (If strict plan discipline is wanted, the two forced test edits could be noted as addenda in `plan.md`, but they're already disclosed in the commit messages.)
- **Decision**: ACCEPTED — no action; all four are mechanical follow-ons or the explicit epilogue deliverable, already disclosed in commit messages and this report.

## Post-Triage — two plan errors caught on stakeholder review (2026-08-05)

The stakeholder reviewed the shipped UI and flagged two places where the plan had asserted a constraint that was never theirs. The implementation had followed the plan faithfully, so these were **plan errors, not implementation drift**; both were fixed in code + tests, and the plan text was corrected in place to read as it should have from the start.

### F6 — Sidebar Logout entry was still present

- **Severity**: 📋 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: src/frontend/src/components/Sidebar.tsx, AppShell.tsx, Sidebar.module.css
- **Detail**: The plan's "What We're NOT Doing" section asserted "Not removing the Sidebar Logout button." That constraint was invented — the change's intent was a single consolidated logout surface in the top-right. The plan text has been corrected in place.
- **Decision**: FIXED — removed the Sidebar Logout `<button>`, the `.bottomItems` group, and the `onLogout` prop from `SidebarProps`; `AppShell` wires `onLogout` into `TopBar` only. `AppShell.test.tsx` asserts the Sidebar nav has no Logout `menuitem` and that the TopBar Logout menuitem fires `onLogout`.

### F7 — ScoreRow action buttons still showed visible text alongside the icons

- **Severity**: 📋 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: src/frontend/src/components/ScoreRow.tsx, ScoreRow.module.css
- **Detail**: Phase 3's contract asserted "Text labels stay for accessibility." That was wrong against intent — the change's aim was to *replace* text with icons. The `aria-label` on each button is what makes dropping visible text safe (icon-only buttons without an accessible name would be an a11y failure). The plan text has been corrected in place.
- **Decision**: FIXED — dropped the visible text from all three buttons; they are now icon-only. CSS reworked to square `2rem × 2rem` hit targets centered on the glyph; icon sizes bumped (`1.15rem` / `1rem`). `ScoreRow.test.tsx` and `ui-chores.spec.ts` assert the buttons have no visible text while still resolving via their `aria-label` accessible names.

### Re-verification after F6/F7

- `make check` green (ruff, both import-linter contracts KEPT, tsc --noEmit).
- `vitest` 135/135 across 20 files.
