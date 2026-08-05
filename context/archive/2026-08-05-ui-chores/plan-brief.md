# UI Chores — Plan Brief

> Full plan: `context/changes/ui-chores/plan.md`
> Research: `context/changes/ui-chores/research.md`

## What & Why

Five small authenticated-UI cleanups, delivered in bulk: a logout dropdown on the top-right nick, icon-ified Preview/Modify/Delete action buttons, a 2-decimal daily-average chart, a PII/LLM-training warning on `/upload`, and a mobile-only "Take a picture" button. The aim is to make the existing UI more user-friendly with minimal, low-risk, frontend-only edits.

## Starting Point

React 18 + TS SPA at `src/frontend/`, plain CSS Modules + `:root` tokens, no icon library (deliberately), no Redux/Context, 760px mobile breakpoint. Logout lives in the Sidebar; the TopBar nick is plain text. The Score-row buttons are text-only (Preview already carries the immutable `target.svg`). The only recharts chart is `<DailyAverageChart>` on the home Dashboard (not on `/scores`). The home `/` Dashboard already renders the same `<ScoreRow>` as `/scores`, so the shared-component edit propagates to both. `--color-warning-*` tokens are wired but unused.

## Desired End State

An authenticated user sees: a hoverable/focusable nick dropdown with Logout in the top-right; icon-leading Preview/Modify/Delete buttons (with text labels) on both `/scores` and the home page; a home-page chart whose Y-axis ticks and tooltip read exactly 2 decimals (e.g. `8.67`); a yellow PII warning callout above two horizontally-arranged `/upload` buttons — "Choose file" (always) and "Take a picture" (mobile-only) — that stack vertically on mobile. `/capture` remains as a fallback.

## Key Decisions Made

| Decision                                | Choice                                                                       | Why (1 sentence)                                                                 | Source   |
| --------------------------------------- | ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------- | -------- |
| Logout dropdown mechanics (#1)          | CSS-only `:hover` + `:focus-within`, no JS state                              | Matches the "pop up while hovering" ask and the minimal-toolchain posture.        | Plan     |
| Icon source for Modify/Delete (#2)      | `react-icons` Bootstrap Icons (`BsPencil`, `BsTrash3`)                       | User-specified; tree-shakeable per-icon; accessible-by-default.                  | Plan     |
| Preview icon (#2)                       | Keep the existing `target.svg`, rendered smaller                             | `target.svg` is immutable per the prior dashboard plan.                          | Research |
| "Main page" requirement (#2)            | No-op beyond editing `ScoreRow.tsx` — home `/` already renders `<ScoreRow>`   | Shared component propagates the change to both surfaces for free.                | Research |
| Chart scope (#3)                        | Chart-only — YAxis `tickFormatter` + Tooltip `formatter`, `.toFixed(2)`       | User chose precision-only; other `.toFixed(1)` sites stay as-is.                 | Plan     |
| "Score dashboard chart" ambiguity (#3)  | Lands on `<DailyAverageChart>` (home `/`), not `/scores` (which has no chart) | The only recharts chart in the codebase is on the home Dashboard.                | Research |
| PII warning wording + force (#4)        | Display-only yellow callout, user's original wording verbatim                | User chose keep-original + display-only (no consent checkbox).                   | Plan     |
| Fate of `/capture` route (#5)           | Keep as fallback (not deleted, not redirected)                               | User chose zero-regression over dead-code cleanup.                               | Plan     |
| `/upload` camera UX (#5)                | Second hidden `<input type="file" capture>` + custom button, mobile-only      | Unifies the page without a JS hook; reuses `Capture.tsx`'s `capture="env"` shape | Plan     |
| Sidebar Logout button                   | Kept (the TopBar dropdown is additive)                                       | No decision to remove it; removing shrinks the sidebar touch target.             | Plan     |

## Scope

**In scope:** TopBar dropdown (`TopBar.tsx`/`.module.css` + `AppShell.tsx` prop thread); `ScoreRow.tsx`/`.module.css` icons; `DailyAverageChart.tsx` formatters; `Upload.tsx`/`.module.css` warning callout + camera button; `react-icons` dep; new/extended vitests.

**Out of scope:** backend/DTO/API changes; `/capture` removal or redirect; 2-decimal formatting beyond the chart; consent checkbox; i18n; Sidebar Logout removal; recharts v3 migration; `target.svg` changes.

## Architecture / Approach

Five sequenced frontend-only phases. Phase 1 (install `react-icons`) is the prerequisite for Phases 3 and 5. Phases 2/3/4 are mutually independent but sequenced for clean commits. Each phase lands as one commit and is independently testable (`make check` + `make fe-test` + a manual browser pass). The `<ScoreRow>` edit in Phase 3 propagates to both `/scores` and the home `/` through the existing shared component. Phase 5 co-locates the warning callout and the camera button because both edit `Upload.tsx`.

## Phases at a Glance

| Phase | What it delivers                                            | Key risk                                                                 |
| ----- | ----------------------------------------------------------- | ------------------------------------------------------------------------ |
| 1     | `react-icons` dependency installed                          | Wrong version pin breaking the build (mitigated: pin `^5.x`, sanity-check) |
| 2     | TopBar nick → CSS hover/focus dropdown with Logout          | a11y — must be keyboard-operable via `:focus-within` (covered by test)   |
| 3     | Icon-leading Preview/Modify/Delete (propagates to `/`)      | Icon sizing inconsistency between `target.svg` and react-icons SVGs      |
| 4     | 2-decimal Y-axis ticks + tooltip on home chart              | Wider tick labels clipping in the chart's `left: -16` margin             |
| 5     | PII callout + mobile-only "Take a picture" on `/upload`     | Camera button visibility boundary matching the 760px stack breakpoint    |

**Prerequisites:** `src/frontend/` installs cleanly (`npm install`); `make check` is green on `master`. No external access, no backend changes, no migrations.
**Estimated effort:** ~1–2 sessions; 5 small commits.

## Open Risks & Assumptions

- **recharts v2 Tooltip `formatter` return shape** — a single-value return vs. a `[value, name]` tuple both work in v2; the implementer picks whichever renders the cleaner series name. (Low risk; both are documented.)
- **Icon visual consistency** — `target.svg` (raster-as-SVG illustration) next to `BsPencil`/`BsTrash3` (line icons) may look like a mixed set; the smaller `.targetIcon` size mitigates but the implementer should eyeball it in Phase 3's manual check.
- **2-decimal tick clipping** — the chart's `margin={{ left: -16 }}` was tuned for integer ticks; `10.00` is wider. Manual check 4.6 verifies; trivial fix (bump `left` margin) if it clips.
- **`react-icons` is a stance shift** — reverses the "minimal toolchain / no icon library" posture from `user-score-dashboard` plan §3.7. This is user-approved (Round 1 of planning), but worth flagging to anyone reviewing the diff who wasn't in the conversation.

## Success Criteria (Summary)

- `make check` + `make fe-test` green across all 5 phases; new vitests cover the dropdown a11y, icon+label presence, formatter output, and the upload warning/capture wiring.
- Manual desktop + ≤760px mobile pass confirms: hover/keyboard dropdown, icons on both pages, 2-decimal chart, verbatim PII warning, mobile-only camera button with vertical stacking, and the `/capture` fallback still working.
