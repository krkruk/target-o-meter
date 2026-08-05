---
date: 2026-08-05T19:29:14+02:00
researcher: ZCode
git_commit: c9e3bcb08d9a9f2a9f87c4c7821b680323f1d410
branch: chore/cleanup-dashboard-implementation
repository: target-o-meter
topic: "5 minor UI cleanups: logout dropdown, icon action buttons, 2-decimal chart, PII upload warning, mobile camera button"
tags: [research, codebase, frontend, ui, recharts, css-modules, responsive, auth]
status: complete
last_updated: 2026-08-05
last_updated_by: ZCode
---

# Research: 5 minor UI cleanups (ui-chores)

**Date**: 2026-08-05T19:29:14+02:00
**Researcher**: ZCode
**Git Commit**: c9e3bcb08d9a9f2a9f87c4c7821b680323f1d410
**Branch**: chore/cleanup-dashboard-implementation
**Repository**: target-o-meter

## Research Question

Identify 5 minor UI issues and fix them in bulk, each scoped to the authenticated experience:

1. Add a **logout dropdown** in the top-right corner that opens on hover over the user's nick.
2. Replace the **Preview / Modify / Delete** text buttons with **icon buttons** in the Score dashboard, and surface the same buttons on the main page.
3. Plot the **chart with 2-decimal precision** (e.g. `0.00`, `21.37`).
4. Add a **yellow warning box** on `/upload` about PII / LLM-training exposure (final wording TBD with the user).
5. Add a **"Take a picture" button** on `/upload` (mobile-only); keep **"Choose file"** everywhere; arrange horizontally with auto-stacking to vertical on mobile.

## Summary

The frontend is a React 18 + TypeScript SPA at `src/frontend/`, styled with **plain CSS Modules** + a global `:root` token palette in `src/frontend/src/styles.css`. There is **no icon library** (no lucide / heroicons / react-icons), **no Tailwind**, **no CSS-in-JS**, **no Context/Redux** — state is plain `useState` with prop drilling, and the only icons today are one SVG asset (`assets/target.svg`) and unicode/emoji glyphs in the Sidebar. The established responsive breakpoint is `@media (max-width: 760px)`, and there is already a `useIsMobile()` matchMedia hook.

All 5 changes are surgical and reuse existing patterns. **Two scope corrections worth confirming with the user before planning:**

- **#2 — "the same buttons shall appear in the main page":** the home `Dashboard` (`/`) *already* renders the same `<ScoreList>` → `<ScoreRow>` component, so Preview/Modify/Delete **already appear on the main page today**. The icon-ification will automatically propagate to both surfaces via the shared component. The "main page" requirement is effectively a no-op beyond making the change inside `ScoreRow.tsx`.
- **#3 — "score dashboard chart":** there is **no chart on the Score dashboard** (`/scores` → `ScoreDashboard.tsx`). The only recharts chart in the codebase is `<DailyAverageChart>` on the **home Dashboard** (`/`). The 2-decimal change lands there. Score-table rows also display `.toFixed(1)` and likely want the same treatment for consistency.

Three of the five (#1 logout dropdown, #4 PII warning, #5 mobile camera button) are **net-new patterns** not grounded in the prior `user-score-dashboard` change — they introduce new conventions, so each carries a small design choice to make in `/10x-plan`.

## Detailed Findings

### #1 — Logout dropdown in top-right (hover over nick)

**Where the nick is displayed today:** `src/frontend/src/components/TopBar.tsx:4-11` — `<header>` with the brand on the left and `<span className={styles.nick}>{nick}</span>` on the right (`:8`). It is **pure display, no click handler**. Styled via `TopBar.module.css:15-18` (grey `#4a4a4a`, 0.95rem).

**Where logout lives today (must move/duplicate to the dropdown):** `src/frontend/src/components/Sidebar.tsx:51-55` — a bottom-pinned `<button role="menuitem" onClick={onLogout}>Logout</button>` (collapses to `⏻` glyph when the sidebar is collapsed). The handler chain: `Sidebar.onLogout` ← `AppShell.onLogout` ← `App.handleLogout` in `src/frontend/src/App.tsx:52-61`, which calls `postLogout()` then `window.location.reload()`.

**The API call to preserve:** `postLogout()` in `src/frontend/src/api.ts:57-67` — `POST /logout` with a CSRF `X-CSRFToken` header; treats 302 as success (Auth0 `/v2/logout` redirect). This is unchanged by the UI move.

**How the frontend knows the user:** `App.tsx:21-29` holds a single `useState<Me | null>`; on mount calls `getMe()` (`GET /v1/me`, `api.ts:40-45`; 401 → `{ authenticated: false, user: null }`). `MeUser` (`api.ts:13-24`) has `nick`, `role` (`'owner'|'user'`), `has_set_nick`. The whole `me` object is prop-drilled: `App` → `AppShell me={me}` (`App.tsx:47`) → `TopBar nick={me.user?.nick}` and `Sidebar isOwner={me.user?.role === 'owner'}` (`AppShell.tsx:32,36`). **No Context** — to wire `onLogout` into `TopBar`, thread it as a prop the same way `Sidebar` already receives it.

**Net-new pattern (no precedent in `user-score-dashboard` docs):** the dropdown-on-hover is new. The closest in-repo precedents for a small disclosure menu are the hand-rolled modals (`BanModal.tsx`, `DeleteUserModal.tsx`, `ModifyModal.tsx`) — overlay + card + Esc-to-dismiss — but a hover dropdown is lighter weight than those (no overlay). Plan decision needed: CSS-only hover (a `:hover`/`:focus-within` group in `TopBar.module.css`) vs. a JS-controlled `useState` toggle. CSS-only is the lower-cost, more conventional fit; `:focus-within` keeps it keyboard-accessible.

**Accessibility note:** a hover-only dropdown is unusable on touch and invisible to some screen readers. Pair `:hover` with `:focus-within` and give the menu a real `<button aria-haspopup="menu" aria-expanded>` trigger rather than a bare `<span>`, so the nick is keyboard-focusable.

### #2 — Icon-ify Preview / Modify / Delete buttons (and "same on main page")

**Buttons live in the shared `<ScoreRow>` component:** `src/frontend/src/components/ScoreRow.tsx:36-63`, one set per row:

- **Preview** (`ScoreRow.tsx:37-46`) — text `"Preview"` already prefixed with `<img src={targetIcon} className={styles.icon} />` (the `target.svg`). Toggles `showPreview` → reveals inline `<ScorePreview>` (`:66-68`). `aria-expanded={showPreview}`.
- **Modify** (`ScoreRow.tsx:47-54`) — text `"Modify"`, opens `<ModifyModal>` (`:70-76`) via `setModal('modify')`.
- **Delete** (`ScoreRow.tsx:55-62`) — text `"Delete"`, className `${styles.actionBtn} ${styles.dangerBtn}` (red), opens `<DeleteModal>` (`:77-83`) via `setModal('delete')`.

**Styling precedent:** `ScoreRow.module.css:43-65` — `.previewBtn`/`.actionBtn` inline-flex, `0.3rem 0.7rem` padding, 6px radius; `.dangerBtn` uses `var(--color-danger)` / `var(--color-danger-border)`. `.icon` is `1rem×1rem` (`:67-70`). The `AdminUsersPage.module.css:36-49, 92-95` row+actions layout is the original template.

**"Main page" requirement is already satisfied structurally.** The home `Dashboard` at `/` (`Dashboard.tsx`, routed at both `/` and `/dashboard` per `AppShell.tsx:46-47`) renders the *same* `<ScoreList>` → `<ScoreRow>` in its "Recent results" region, fed by `getScores({page:1, page_size:20})` (`Dashboard.tsx:108-114`), with its own `handleModified`/`handleDeleted` callbacks (`:66-73`). So any change inside `ScoreRow.tsx` propagates to **both** `/scores` and `/` for free. No separate edit point on the main page is needed.

**Icon source — the key design decision.** There is **no icon library installed** (`package.json:17-23`: only react, react-dom, react-router-dom, recharts, vanilla-cookieconsent). `user-score-dashboard` plan.md (§3.7, lines 325-327) **deliberately avoided an icon library** and the team's stated toolchain posture is "minimal" (`styles.css:5-7`: *"No Tailwind, no CSS-in-JS — the plan §3.7 keeps the toolchain minimal."*). Two paths:

- **(A) Keep zero deps — emoji/unicode glyphs or new inline SVGs.** Matches the existing Sidebar convention (`⌂`, `🎯`, `⚙`, `⏻`). Lowest disruption; weakest visual consistency. Preview would keep its existing `target.svg`.
- **(B) Introduce a small icon library (e.g. `lucide-react`, tree-shakeable, ~1KB/icon used).** Cleaner, accessible-by-default (`aria-label`/`title` support), but it's a new dependency that reverses the "minimal toolchain" stance — a posture shift the user should sign off on, not a silent decision.

Either way, the labels must remain (or move to `aria-label`/`title`) for accessibility — icon-only buttons without text labels fail WCAG. Recommend keeping the text label *and* adding the icon (icon + label), OR icon-only with a robust `aria-label` and a `title` tooltip. `target.svg` is **immutable per the prior plan** (plan.md:52, 325-327) and must be preserved on Preview.

### #3 — Chart plotted with 2-decimal precision

**Scope correction:** the request says "score dashboard", but **there is no chart on the Score dashboard** (`/scores` → `ScoreDashboard.tsx` is a paginated table only, values at `ScoreRow.tsx:34`). The only recharts chart in the codebase is `<DailyAverageChart>` on the **home Dashboard** (`/`).

**The chart:** `src/frontend/src/components/DailyAverageChart.tsx:7-15` imports `LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer` from `recharts` (v`^2.15.4`, `package.json:21`; note v2 is flagged deprecated in the lockfile in favor of v3). Rendered at `DailyAverageChart.tsx:50-64`:

```jsx
<ResponsiveContainer width="100%" height="100%" minHeight={160}>
  <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: -16 }}>
    <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
    <XAxis dataKey="date" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
    <YAxis domain={[0, 10]} tick={{ fontSize: 10 }} />
    <Tooltip />
    <Line type="monotone" dataKey="average" stroke="var(--color-primary)" strokeWidth={2} dot={false} />
  </LineChart>
</ResponsiveContainer>
```

- **No `tickFormatter`** on `<YAxis>` (line 54) — default ticks render `0, 2, 4, 6, 8, 10`. YAxis plots the `average` domain `[0,10]`; XAxis is `dataKey="date"` (unaffected by a decimal ask).
- **`<Tooltip />` is bare** (line 55) — no `formatter`/`labelFormatter`/`content`; recharts default shows the raw `average` (could be `7.333...`).

**Edit points for 2-decimal:**

| What | Where | Change |
|---|---|---|
| YAxis ticks | `DailyAverageChart.tsx:54` | add `tickFormatter={(v) => Number(v).toFixed(2)}` |
| Tooltip value | `DailyAverageChart.tsx:55` | add `formatter={(value) => [Number(value).toFixed(2), 'Average']}` (recharts v2 2-tuple signature keeps default styling) |
| aria-label min/max/mean | `DailyAverageChart.tsx:34-36` | `.toFixed(1)` → `.toFixed(2)` for screen-reader consistency |

**Data origin (sanity-check, no change needed):** `daily` prop ← `Dashboard.tsx:117` ← `aggregations.daily_averages` ← `getAggregations()` (`api.ts:229-235`, `GET /v1/scores/aggregations`) ← `Aggregations.daily_averages: DailyAverage[]` (`api.ts:204-208`) ← `{ date: str; average: float }` (`api.ts:199-202`) ← backend `DailyAverageDTO` (`src/domains/vision/dtos.py:145-149`) computed by grouping accepted results per day and averaging `score_average` (`src/domains/vision/services.py:717-739`). Each plotted point is a true float mean in `[0,10]` — exactly the case that needs fixed precision. **Do not regress the 0–10 canonical scale** (research.md:95,289 warns a stale `mocks/dashboard.ts` fixture used 0–100).

**Shared formatting helper (recommend):** there are **5 other `.toFixed(1)` sites** with no shared util — `HeroStats.tsx:20,24`, `ScoreRow.tsx:34`, and the 3 aria-label lines above. If the goal is consistent 2-decimal everywhere, add one `formatScore(n) => n.toFixed(2)` (new tiny module under `src/frontend/src/`, e.g. `format.ts`, or co-locate in `api.ts`) and wire it into all sites. Decide scope in `/10x-plan`: chart-only, or all score displays.

### #4 — Yellow PII warning box on `/upload`

**The route + input:** `/upload` → `<Upload>` (`AppShell.tsx:50`; `src/frontend/src/components/Upload.tsx`). The file input is at `Upload.tsx:47-55` — a bare native `<input type="file" accept="image/*">` with `aria-label="Select a photo of your target"`. There is **no custom "Choose file" button** — it relies on the browser's `::file-selector-button`, styled in `Upload.module.css:19-33` (`0.6rem 1.25rem` padding, `var(--color-primary)` background, white text, 8px radius). The visible label is the browser default ("Choose file"/"Browse" depending on browser/locale).

**Reusable tokens already wired but unused.** `src/frontend/src/styles.css:30-32` defines `--color-warning-bg: #fef3c7`, `--color-warning-border: #f59e0b`, `--color-warning-text: #78350f`, commented as *"reminder callout surface"* — but **no component consumes them yet**. This warning box is their first consumer; no new tokens needed.

**Closest existing warning patterns to mirror (not a standalone component today):**
- `DeleteModal.tsx:63-65` — `<p className={styles.warning}>This permanently removes…</p>` inside a modal card (`.warning` in `DeleteModal.module.css`). **Strongest precedent** — a warning paragraph in a form/card context.
- Inline error pattern (different palette, but same shape): `<p className={styles.error} role="alert">{error}</p>` (`NickPrompt.tsx:63`, `BanModal.tsx:105`, `ModifyModal.tsx:212`, `Upload.tsx:56-60`).
- Page-level `<div role="alert">` (`Dashboard.tsx:82`, `ScoreDashboard.tsx:63`, `AdminUsersPage.tsx:105`).

**Recommended shape:** a new small reusable component (e.g. `WarningCallout.tsx` + `.module.css`) consuming `--color-warning-bg/border/text`, or a one-off `<div role="note" className={styles.warning}>` in `Upload.tsx`. A reusable component is justified if #4's wording reappears elsewhere (e.g. on `/capture`).

**Wording — needs user sign-off (the request explicitly asks to discuss).** Candidate draft, tightened from the user's note:

> ⚠️ **Heads up:** Uploaded target photos are used to train our scoring models and may become effectively public. **Do not upload anything containing Personal Identifiable Information** (faces, names, addresses, QR/barcodes, club IDs). By uploading, you accept this risk — please proceed responsibly.

Open wording choices for `/10x-plan`: severity (caution vs. warning vs. info), whether to gate upload behind an explicit "I understand" checkbox (stronger consent) vs. just displaying the note, and whether the same warning must also appear on the mobile `/capture` route (currently separate — see #5).

### #5 — "Take a picture" button (mobile-only) + horizontal layout

**Current behavior — already split across two routes, which #5 wants to unify.**
- **PC (`/upload`):** `<input type="file" accept="image/*">`, **no `capture`** (`Upload.tsx:47-55`) — opens the OS file dialog. ✅ matches the desired "Choose file" PC behavior.
- **Mobile (`/capture`):** `<input type="file" accept="image/*" capture="environment">` (`Capture.tsx:50-60`) — **forces the camera**, with no way to pick an existing file.
- The home `Dashboard` picks the route via the existing **`useIsMobile()`** hook (`Dashboard.tsx:22-26`): `navigate(isMobile ? '/capture' : '/upload')` (`Dashboard.tsx:76`). The hook uses `window.matchMedia('(max-width: 760px)')` with a jsdom guard; tests stub `matchMedia` (`Dashboard.test.tsx:19,48`).

**What the user wants flips the current model:** instead of two routes chosen by device, **one `/upload` page** with two buttons — "Choose file" (always visible, file dialog) and "Take a picture" (mobile-only, camera). Two implementation shapes:

- **(A) Two `<input type="file">` elements** in `Upload.tsx` — one plain (Choose file), one with `capture="environment"` (Take a picture). The camera input is hidden via CSS on `>760px`. **No new JS**; both inputs POST to the same handler. Cleanest fit for the stated "unify the behavior" goal.
- **(B) Keep the `/capture` route** and add a navigation button. More moving parts, doesn't truly unify — rejected unless there's a reason to preserve `/capture` (e.g. analytics, deep-links).

**Layout requirement (horizontal → vertical on mobile):** standard flex pattern, already used elsewhere (e.g. `AdminUsersPage.module.css` `.actions`):
```css
.uploadActions { display: flex; flex-direction: row; gap: 0.75rem; }
@media (max-width: 760px) { .uploadActions { flex-direction: column; } }
```
The 760px breakpoint is the established convention (`Dashboard.module.css:72`, `Welcome.module.css:121`, `HeroStats.module.css:31`). Use the same value for the "Take a picture" show/hide so the camera button only appears when the layout is also stacking — keeps behavior consistent at one breakpoint.

**`useIsMobile()` is reusable** if a JS-driven show/hide is preferred over CSS, but CSS media queries are simpler and test-friendly (no `matchMedia` stub needed for the upload page). Recommend CSS-only for #5 and reserving `useIsMobile()` for routing-level decisions.

**Consequence to flag:** `/capture` becomes dead code under option (A). The plan should decide whether to delete `Capture.tsx` + its route (`AppShell.tsx:49`) and the `Dashboard.tsx:76` branching, or keep it as a fallback. Deleting is cleaner; keeping avoids a routing regression if anything deep-links to `/capture`.

## Code References

- `src/frontend/src/components/TopBar.tsx:4-11` — header; nick `<span>` at `:8` (target for #1 dropdown).
- `src/frontend/src/components/TopBar.module.css:15-18` — `.nick` styling.
- `src/frontend/src/components/AppShell.tsx:32,34-61` — authenticated shell; prop wiring + routes table (`:45-57`).
- `src/frontend/src/components/Sidebar.tsx:51-55` — current Logout button (handler source for #1).
- `src/frontend/src/App.tsx:21-29,47,52-61` — `me` state, `getMe()`, prop-drill, `handleLogout`.
- `src/frontend/src/api.ts:13-24,40-45,57-67` — `Me`/`MeUser`, `getMe`, `postLogout`.
- `src/frontend/src/components/ScoreRow.tsx:34,36-63,66-83` — per-row buttons (#2) and the `toFixed(1)` score (#3).
- `src/frontend/src/components/ScoreRow.module.css:43-70` — button + icon styling precedent.
- `src/frontend/src/components/Dashboard.tsx:22-26,66-73,76,108-114,117` — home Dashboard; `useIsMobile`, same `<ScoreList>`, route-branch, chart wiring.
- `src/frontend/src/components/ScoreDashboard.tsx` — `/scores` table page (no chart).
- `src/frontend/src/components/DailyAverageChart.tsx:7-15,34-36,50-64` — the only recharts chart; #3 edit points.
- `src/frontend/src/components/HeroStats.tsx:20,24` — other `toFixed(1)` sites (#3 scope decision).
- `src/frontend/src/components/Upload.tsx:47-55` + `Upload.module.css:19-33` — file input + `::file-selector-button` styling (#4, #5).
- `src/frontend/src/components/Capture.tsx:50-60` — mobile camera route (becomes redundant under #5 option A).
- `src/frontend/src/components/DeleteModal.tsx:63-65` — strongest warning-paragraph precedent (#4).
- `src/frontend/src/styles.css:5-7,16-56` — toolchain posture + global tokens incl. **unused** `--color-warning-*` (`:30-32`).
- `src/frontend/package.json:17-23` — deps (no icon library).
- `src/frontend/assets/target.svg` — the one SVG asset; **immutable** per `user-score-dashboard` plan.md:52,325-327 (must stay on Preview in #2).
- Backend (no change required, sanity-check only for #3): `src/domains/vision/dtos.py:145-157`, `src/domains/vision/services.py:717-739`.

## Architecture Insights

- **Styling posture is deliberately minimal** (`styles.css:5-7`): plain CSS Modules + `:root` tokens, no Tailwind, no CSS-in-JS. Any change should add a `.module.css` + reuse `--color-*` tokens, not introduce a new styling system.
- **No icon library, by choice** (`user-score-dashboard` plan.md §3.7). #2's icon-ification is the first decision that can either preserve that posture (emoji/inline SVG) or break it (add `lucide-react`-style dep). User sign-off needed either way.
- **Single shared `<ScoreRow>`** powers both `/scores` and `/`. Edits to action buttons propagate to both surfaces automatically — the "main page" half of #2 is structurally free.
- **`useIsMobile()` (matchMedia 760px) + `@media (max-width: 760px)`** are the established responsive pair. #5 should reuse the 760px breakpoint for both the camera-button visibility and the horizontal→vertical stack.
- **Hand-rolled modals/disclosures** are the convention (no shared `<Modal>` primitive). A hover dropdown (#1) is lighter than a modal; CSS `:hover`+`:focus-within` is the lower-cost, accessible-enough fit.
- **`--color-warning-*` tokens are reserved-but-unused** — #4 is their first consumer, so the warning box lands without new tokens.
- **recharts is v2 (deprecated in lockfile for v3).** Stay on v2 for these edits; the `tickFormatter`/`formatter` API is identical across v2/v3 so no migration pressure is introduced.

## Historical Context (from prior changes)

- `context/changes/user-score-dashboard/research.md:190-203,280` — confirmed no icon library; Sidebar uses emoji glyphs; `target.svg` is the only raster icon, used as Preview's `<img>`.
- `context/changes/user-score-dashboard/plan.md:52,325-327` — **`target.svg` is immutable** and must remain the Preview icon (constraint for #2).
- `context/changes/user-score-dashboard/plan.md:64` — confirmed `useState` + `fetch`, CSS Modules with `var(--color-*)`, hardcoded labels (no i18n), hand-rolled modals mirroring `BanModal`.
- `context/changes/user-score-dashboard/research.md:223` — mobile breakpoint `@media (max-width: 760px)` is the convention (constraint for #5).
- `context/changes/user-score-dashboard/research.md:95,289` — canonical scale is **0–10**; do not regress to the stale 0–100 fixture (constraint for #3).
- `context/changes/user-score-dashboard/research.md:214`, `plan.md:325` — rows use `.toFixed(1)` today (baseline for #3's scope decision).
- No prior change addresses: a top-right logout dropdown (#1), an upload PII warning (#4), or unifying upload/capture into one page (#5). All three are net-new patterns.

## Related Research

- `context/changes/user-score-dashboard/research.md` — the dashboard's foundational frontend research (component map, conventions).
- `context/changes/user-score-dashboard/plan.md` — the dashboard implementation plan (button row, modal patterns, immutability of `target.svg`).
- `context/foundation/lessons.md` — no frontend-UI lessons recorded yet (all four entries are backend: Railpack, one-class-per-file, REST URI naming, faulthandler). The icon-library and 760px-breakpoint conventions live in the dashboard research/plan, not in lessons.

## Open Questions

1. **#1 dropdown trigger mechanics** — CSS-only (`:hover` + `:focus-within`) vs. JS `useState` toggle? (Recommend CSS-only for cost; ensure `<button aria-haspopup aria-expanded>` for a11y.)
2. **#2 icon source** — introduce `lucide-react` (new dep, reverses "minimal toolchain" posture, needs user sign-off) vs. emoji/inline SVGs (matches Sidebar convention)? Keep labels as icon+text, or icon-only with `aria-label`/`title`?
3. **#3 scope** — chart-only (YAxis `tickFormatter` + Tooltip `formatter`), or also the 5 other `.toFixed(1)` display sites via a shared `formatScore` helper? (User's `21.37` example implies >10, which the 0–10 scale can't produce — confirm whether they're illustrating precision vs. expecting a different metric.)
4. **#4 wording + force** — finalize the PII note text (draft above) and decide display-only vs. explicit "I understand" consent checkbox. Does the same warning also go on `/capture`?
5. **#5 `/capture` fate** — delete `Capture.tsx` + route + `Dashboard.tsx:76` branch (cleaner), or keep as fallback (avoids deep-link regression)? Confirm "Take a picture" uses a second `<input type="file" capture>` on the same `/upload` page (option A).
6. **"Main page" (#2)** — confirm the user realizes the home Dashboard already shows the same Preview/Modify/Delete buttons via the shared `<ScoreRow>`; the icon change is the only actual work.
