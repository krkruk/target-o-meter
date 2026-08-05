# UI Chores — Implementation Plan

## Overview

Five small authenticated-UI cleanups, delivered in bulk: (1) a logout dropdown that opens on hover/focus over the user's nick in the top-right; (2) icon-ify the Preview/Modify/Delete action buttons (propagates to both `/scores` and the home `/` via the shared `<ScoreRow>`); (3) plot the daily-average chart with 2-decimal precision; (4) a yellow PII/LLM-training warning callout on `/upload`; (5) a mobile-only "Take a picture" button on `/upload` with a horizontal layout that stacks vertically on mobile.

## Current State Analysis

Frontend is React 18 + TS at `src/frontend/`, styled with **plain CSS Modules** + `:root` tokens in `src/frontend/src/styles.css`. **No icon library** (deliberately — `user-score-dashboard` plan §3.7). **No Redux/Context** — plain `useState` + prop drilling. Established responsive breakpoint: `@media (max-width: 760px)`, plus a `useIsMobile()` matchMedia hook used only for route branching on the home Dashboard.

- Nick display: `TopBar.tsx:4-11` — bare `<span className={styles.nick}>{nick}</span>`, **no click handler**, props `{ nick: string }` only.
- Logout today: `Sidebar.tsx:51-55` (`onClick={onLogout}`); handler chain `App.handleLogout` (`App.tsx:52-61`) → `postLogout()` (`api.ts:57-67`, `POST /logout`). `onLogout` is threaded into `Sidebar` at `AppShell.tsx:41` but **not** into `TopBar`.
- Action buttons: `ScoreRow.tsx:37-62` — Preview already carries `<img src={targetIcon} className={styles.icon} />` (the immutable `target.svg`); Modify/Delete are text-only. `.actionBtn` already has `display: inline-flex; gap: 0.3rem` so leading icons need no layout change.
- Chart: `DailyAverageChart.tsx:50-64` — the **only** recharts chart in the codebase, on the **home** Dashboard (not `/scores`, which has no chart). YAxis (line 54) and Tooltip (line 55) have no formatters.
- Upload input: `Upload.tsx:47-55` — bare `<input type="file" accept="image/*">`, no `capture`, no custom button (relies on `::file-selector-button`, styled at `Upload.module.css:19-33`). Selection fires the job immediately via `handleFile` → `createScoringJob`. **`useIsMobile` is NOT imported here.**
- Mobile camera route: `Capture.tsx:50-60` — `<input type="file" accept="image/*" capture="environment">`. **Kept as fallback** per decision (not deleted).
- `--color-warning-bg/border/text` tokens exist at `styles.css:30-32` (commented "reminder callout surface") but are **unused** — the PII callout is their first consumer.

### Key Discoveries:

- The home `/` Dashboard already renders the same `<ScoreList>` → `<ScoreRow>` (`Dashboard.tsx:108-114`), so #2's "main page" requirement is structurally free — editing `ScoreRow.tsx` propagates to both surfaces.
- `react-icons` Bootstrap Icons (`react-icons/bs`) is tree-shakeable per-icon; canonical names: `BsPencil` (Modify), `BsTrash3` (Delete — v1.8+ renamed `BsTrash`→`BsTrash3`; `BsTrash` remains an alias).
- `Capture.tsx:17` already declares `inputRef` (currently unused) — a hidden-input + custom-button pattern can reuse it.
- recharts is v2 (`^2.15.4`); the `tickFormatter`/`formatter` API is identical in v3, so no migration pressure.

## Desired End State

An authenticated user sees: a nick in the top-right that, on hover or keyboard focus, reveals a small dropdown with a Logout action. In both the Score dashboard and the home page's recent-results list, each score row's Preview/Modify/Delete buttons lead with an icon (Preview keeps its target graphic, smaller; Modify/Delete get Bootstrap pencil/trash icons) alongside the text label. The daily-average chart on the home page shows Y-axis ticks and tooltip values at exactly 2 decimals (e.g. `8.67`). On `/upload`, a yellow warning callout explains the PII/LLM-training risk above two horizontally-arranged buttons — "Choose file" (always visible) and "Take a picture" (visible only on mobile, ≤760px) — which stack vertically on mobile. The `/capture` route still works as a fallback.

**Verify by:** `make check` (lint + type-check + import contracts) green; `make fe-test` green (existing `Dashboard.test.tsx` matchMedia stubs still pass; any new vitest for the dropdown/a11y passes); manual browser pass on desktop + a narrow (≤760px) viewport confirming hover dropdown, icon buttons, 2-decimal chart ticks/tooltip, warning callout text, and the mobile-only camera button + vertical stacking.

## What We're NOT Doing

- **Not removing `/capture`** — kept as a fallback route per decision. `Capture.tsx`, its `AppShell.tsx` route entry, and the `Dashboard.tsx:76` isMobile branch stay as-is.
- **Not changing backend / DTOs / API contracts** — all five changes are frontend-only. The chart's 2-decimal formatting is display-only; the backend `average: float` (`dtos.py:145-149`) is unchanged.
- **Not changing the score scale** — canonical 0–10 (`research.md:95,289` warns against regressing to the stale 0–100 fixture).
- **Not adding 2-decimal formatting beyond the chart** — `HeroStats.tsx:20,24`, `ScoreRow.tsx:34`, and the chart's aria-label `.toFixed(1)` sites stay as-is per the "chart-only" decision.
- **Not introducing a consent checkbox** on the PII warning — display-only per decision.
- **Not removing the Sidebar Logout button** — the new TopBar dropdown is additive; the Sidebar entry stays (no decision was made to remove it, and removing it would shrink the touch target for sidebar users).
- **Not adding i18n** — labels stay hardcoded inline (matches existing convention; no i18n in the codebase).
- **Not touching `target.svg`** — immutable per `user-score-dashboard` plan.md:52,325-327.
- **Not migrating recharts to v3** — out of scope; stay on v2.

## Implementation Approach

Five sequenced phases. Phase 1 (dep install) is the prerequisite for Phases 3 and 5. Phases 2/3/4 are independent of each other but sequenced for clean commits. Each phase is independently testable and lands as one commit. The shared `<ScoreRow>` edit in Phase 3 propagates to both `/scores` and `/` for free. Phase 5 co-locates the warning callout and the camera button because both edit `Upload.tsx` + `Upload.module.css`.

## Phase 1: Add `react-icons` dependency

### Overview

Install `react-icons` so Phases 3 (Modify/Delete icons) and 5 (camera/upload button icons) can import Bootstrap Icons. Done in isolation so the dep addition isn't entangled with UI logic and the import-linter/type-checker can confirm the package resolves before any component imports it.

### Changes Required:

#### 1.1 Install react-icons

**File**: `src/frontend/package.json`

**Intent**: Add `react-icons` as a runtime dependency so Bootstrap Icons (`BsPencil`, `BsTrash3`, and the camera/upload icons used in Phase 5) are importable. This is the first new runtime dep since `recharts` and reverses the "no icon library" stance from `user-score-dashboard` plan §3.7 — a deliberate, user-approved stance shift.

**Contract**: Run the project's package manager install from `src/frontend/` (npm, per `package-lock.json`). Pin to the latest stable `^5.x` (React-18-compatible). After install, `react-icons` appears in `dependencies` alongside `react`/`react-dom`/`react-router-dom`/`recharts`/`vanilla-cookieconsent`. No component imports it yet in this phase — that comes in Phases 3 and 5.

### Success Criteria:

#### Automated Verification:

- `cd src/frontend && npm install react-icons` completes; `package.json` + `package-lock.json` updated.
- `make check` passes (confirms no lint/type regressions from the dep alone).
- A throwaway import sanity-check compiles: `node -e "require('react-icons/bs')"` (or `tsc --noEmit` on a scratch file importing `BsPencil`) resolves without error — delete the scratch check afterward.

#### Manual Verification:

- `git diff src/frontend/package.json` shows only the `react-icons` line added under `dependencies`.

**Implementation Note**: After this phase and `make check` pass, pause for manual confirmation that the install is clean before proceeding.

---

## Phase 2: Logout dropdown in TopBar

### Overview

Convert the top-right nick into a CSS-only disclosure that reveals a small menu containing a Logout action on hover and keyboard focus. Thread `onLogout` from `AppShell` into `TopBar`.

### Changes Required:

#### 2.1 Thread onLogout into TopBar

**File**: `src/frontend/src/components/AppShell.tsx`

**Intent**: `TopBar` currently receives only `nick` (`AppShell.tsx:36`); it needs `onLogout` to render the Logout menu item. Thread the same `onLogout` prop that `Sidebar` already receives (`AppShell.tsx:41`) into the `TopBar` call.

**Contract**: Line 36 changes from `<TopBar nick={me.user?.nick ?? ''} />` to also pass `onLogout={onLogout}`. No other AppShell change.

#### 2.2 Add the dropdown markup + CSS-only disclosure

**File**: `src/frontend/src/components/TopBar.tsx`

**Intent**: Replace the bare `<span>{nick}</span>` (`TopBar.tsx:8`) with an accessible disclosure: a `<button>` trigger showing the nick, wrapped in a group whose menu becomes visible on `:hover` and `:focus-within`. The menu contains a single Logout `<button>` wired to `onLogout`. No JS state — pure CSS.

**Contract**: Props expand from `{ nick: string }` to `{ nick: string; onLogout: () => void }`. The trigger is `<button type="button" className={styles.nickTrigger} aria-haspopup="menu" aria-expanded="false">{nick}</button>` (aria-expanded stays false; this is a CSS disclosure, not a JS toggle — the menu visibility is controlled by `.nickGroup:hover .menu` and `.nickGroup:focus-within .menu`). The menu is a `<div role="menu" className={styles.menu}>` containing `<button role="menuitem" onClick={onLogout}>Logout</button>`. Markup is wrapped in a `<div className={styles.nickGroup}>`.

#### 2.3 Dropdown CSS

**File**: `src/frontend/src/components/TopBar.module.css`

**Intent**: Style the disclosure group, trigger, and flyout menu using existing tokens. The menu is absolutely positioned under the nick, hidden by default, revealed on group `:hover`/`:focus-within`.

**Contract**: Add `.nickGroup { position: relative; }`, `.nickTrigger { font-size: 0.95rem; color: #4a4a4a; background: none; border: none; cursor: pointer; font: inherit; }` (preserves the current `.nick` visual at `TopBar.module.css:15-18`). Add `.menu { position: absolute; right: 0; top: 100%; min-width: 8rem; background: #fff; border: 1px solid var(--color-border); border-radius: 6px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); padding: 0.25rem; opacity: 0; visibility: hidden; transform: translateY(-4px); transition: opacity 120ms, transform 120ms, visibility 120ms; }`. Add `.nickGroup:hover .menu, .nickGroup:focus-within .menu { opacity: 1; visibility: visible; transform: translateY(0); }`. Menu items reuse button styling; Logout item can carry `.dangerBtn`-equivalent color via `color: var(--color-danger)` if desired (optional — keep neutral to match the Sidebar Logout which is neutral). Remove or repurpose the old `.nick` rule.

### Success Criteria:

#### Automated Verification:

- `make check` passes (lint + type-check + import contracts).
- `make fe-test` passes — existing `Dashboard.test.tsx`/`AppShell` tests still green (the prop change to TopBar must not break any snapshot/assertion).
- A new vitest (co-located, e.g. `TopBar.test.tsx`) asserts: (a) the Logout button is **not** present in the default render's accessibility tree until the group receives focus, and (b) clicking the Logout button calls the `onLogout` prop. (CSS `:hover`/`:focus-within` visibility isn't assertable in jsdom — assert on the click handler wiring and the `aria-haspopup`/`role="menu"` markup instead.)

#### Manual Verification:

- Desktop browser: hovering the nick reveals the Logout menu; moving the mouse into the menu keeps it open; clicking Logout posts `/logout` and reloads (existing `postLogout` flow).
- Keyboard: Tab to the nick trigger focuses it and reveals the menu (`:focus-within`); Tab again moves into the Logout item; Enter activates it.
- No layout shift in the header; the nick's visual position is unchanged.

**Implementation Note**: After Phase 2 + automated verification pass, pause for manual confirmation of the hover/keyboard behavior before proceeding.

---

## Phase 3: Icon-ify Preview/Modify/Delete buttons

### Overview

Add leading icons to the three action buttons in `<ScoreRow>`. Preview keeps the existing `target.svg` (rendered smaller). Modify and Delete get `BsPencil` and `BsTrash3` from `react-icons/bs`. Text labels stay for accessibility. This propagates to both `/scores` and the home `/` via the shared component.

### Changes Required:

#### 3.1 Import the Bootstrap icons

**File**: `src/frontend/src/components/ScoreRow.tsx`

**Intent**: Import the two icons used by Modify/Delete from the Bootstrap Icons pack. Preview keeps its existing `target.svg` import at line 12.

**Contract**: Add `import { BsPencil, BsTrash3 } from 'react-icons/bs';`. These render as inline SVGs sized via `1em` and inheriting `color: currentColor`, so they pick up `.dangerBtn`'s `color: var(--color-danger)` for Delete automatically.

#### 3.2 Render icons inside the buttons

**File**: `src/frontend/src/components/ScoreRow.tsx`

**Intent**: Add a leading icon to Modify and Delete, and shrink the existing Preview `target.svg`. The existing `.actionBtn` already has `display: inline-flex; gap: 0.3rem` (`ScoreRow.module.css:43-54`) so no layout change is needed.

**Contract**:
- Modify button (lines 47-54): insert `<BsPencil className={styles.icon} aria-hidden="true" />` as the first child, before the `Modify` text.
- Delete button (lines 55-62): insert `<BsTrash3 className={styles.icon} aria-hidden="true" />` as the first child, before the `Delete` text.
- Preview button (lines 37-46): the existing `<img src={targetIcon} alt="" className={styles.icon} />` stays — add a smaller variant class (e.g. `className={`${styles.icon} ${styles.targetIcon}`}`) and size it down so it reads as an icon, not a hero graphic.
- `aria-hidden="true"` on the icons because each button already has a descriptive `aria-label` (e.g. `aria-label={\`Modify score from ${dateLabel}\`}`) — the icon is decorative.

#### 3.3 Adjust icon sizing CSS

**File**: `src/frontend/src/components/ScoreRow.module.css`

**Intent**: Ensure all three icons render at a consistent icon size. The existing `.icon` rule (`ScoreRow.module.css:67-70`) is `1rem × 1rem` — keep it for the react-icons SVGs (they honor explicit width/height). Add a smaller rule for the Preview target graphic.

**Contract**: Keep `.icon { width: 1rem; height: 1rem; }`. Add `.targetIcon { width: 0.85rem; height: 0.85rem; }` (or similar) to render the Preview `target.svg` smaller than its current 1rem so the three icons read as a consistent set. No other CSS change — the buttons' inline-flex + gap already accommodate a leading icon.

### Success Criteria:

#### Automated Verification:

- `make check` passes.
- `make fe-test` passes — any existing `ScoreRow`/`ScoreDashboard`/`Dashboard` tests that assert on button text still pass (text labels are unchanged; only a leading icon node is added).
- A new or extended vitest asserts each of the three buttons renders an icon (svg/img) node alongside its text label, and that the existing `aria-label`s are preserved.

#### Manual Verification:

- `/scores` page: each row shows Preview (small target graphic + "Preview"), Modify (pencil + "Modify"), Delete (trash + "Delete").
- Home `/` page "Recent results": same row appearance (confirms propagation through the shared `<ScoreRow>`).
- Delete icon picks up the danger color; Modify icon is neutral.
- Buttons remain keyboard-operable; screen reader announces the `aria-label`, not the icon.

**Implementation Note**: After Phase 3 + automated verification pass, pause for manual confirmation of the icon appearance on both pages before proceeding.

---

## Phase 4: 2-decimal chart formatting

### Overview

Display Y-axis ticks and tooltip values on the daily-average chart with exactly 2 decimal places. Chart-only; no other score-display sites change.

### Changes Required:

#### 4.1 Add tickFormatter to YAxis

**File**: `src/frontend/src/components/DailyAverageChart.tsx`

**Intent**: The `<YAxis>` at line 54 currently renders default integer ticks (`0, 2, 4, 6, 8, 10`). Add a `tickFormatter` so ticks read `0.00, 2.00, …, 10.00`.

**Contract**: Line 54 changes to `<YAxis domain={[0, 10]} tick={{ fontSize: 10 }} tickFormatter={(v) => Number(v).toFixed(2)} />`. `Number(v)` guards against the value arriving as a string. XAxis (line 53, `dataKey="date"`) is unchanged.

#### 4.2 Add formatter to Tooltip

**File**: `src/frontend/src/components/DailyAverageChart.tsx`

**Intent**: The bare `<Tooltip />` at line 55 shows the raw `average` (potentially many decimals). Add a `formatter` so the tooltip reads `8.67` (and a stable series name).

**Contract**: Line 55 changes to `<Tooltip formatter={(value) => Number(value).toFixed(2)} />`. (recharts v2 accepts a single-value-returning formatter for the value; if the default series name "average" reads poorly, return a 2-tuple `[Number(value).toFixed(2), 'Average']` — implementer's call based on what renders cleanly. Both signatures are valid in v2.)

### Success Criteria:

#### Automated Verification:

- `make check` passes.
- `make fe-test` passes — existing `DailyAverageChart` tests (if any) still green; the data shape is unchanged.
- A new or extended vitest asserts the chart passes `tickFormatter`/`formatter` (e.g. render with mock data `{date:'2026-08-01', average: 7.3333}` and assert the formatter output `'7.33'` appears, or unit-test the formatter function directly).

#### Manual Verification:

- Home `/` page chart: Y-axis ticks show `0.00, 2.00, 4.00, 6.00, 8.00, 10.00`.
- Hovering a data point shows the tooltip value at 2 decimals (e.g. `7.33`), not `7.333333`.
- Chart layout/spacing unchanged; the wider tick labels don't clip (the chart already has `margin={{ left: -16 }}` — verify the 2-decimal ticks still fit; adjust `left` margin if they clip).

**Implementation Note**: After Phase 4 + automated verification pass, pause for manual confirmation of the chart appearance before proceeding.

---

## Phase 5: PII warning + mobile camera button on `/upload`

### Overview

Add a yellow PII/LLM-training warning callout (verbatim user wording) and restructure the upload input into two horizontally-arranged buttons — "Choose file" (always) and "Take a picture" (mobile-only, ≤760px) — that stack vertically on mobile. The `/capture` route stays as a fallback.

### Changes Required:

#### 5.1 Add the PII warning callout

**File**: `src/frontend/src/components/Upload.tsx`

**Intent**: Surface the data-handling risk above the upload buttons, using the user's verbatim wording and the already-wired-but-unused `--color-warning-*` tokens (`styles.css:30-32`).

**Contract**: Above the file-input button group, render `<div className={styles.warning} role="note">` containing the verbatim text: *"The data is used to train LLM models. Do not upload Personal Identifiable Information. By uploading the image, you agree to effectively make this information public. Think about it and proceed responsibly."* (per the user's "keep my original" decision — do not paraphrase). `role="note"` (not `role="alert"`, which is for errors); the callout is purely informational.

#### 5.2 Restructure into two custom buttons + hidden inputs

**File**: `src/frontend/src/components/Upload.tsx`

**Intent**: Replace the single native `<input type="file">` (lines 47-55) with a horizontal button group containing two custom-styled buttons, each backed by a hidden native `<input type="file">` triggered via a ref + `.click()`. "Choose file" is always visible; "Take a picture" is mobile-only (its button/input hidden at >760px via CSS). Both feed the existing `handleFile` → `createScoringJob` flow unchanged.

**Contract**:
- Add `const fileInputRef = useRef<HTMLInputElement>(null);` and `const cameraInputRef = useRef<HTMLInputElement>(null);` (the existing `useRef` import at line 1 — confirm it's imported; if not, add it to the react import).
- "Choose file": a `<button type="button" onClick={() => fileInputRef.current?.click()}>` containing an optional `<BsUpload />` icon + the label "Choose file", followed by a visually-hidden `<input ref={fileInputRef} type="file" accept="image/*" aria-label="Select a photo of your target" onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }} />` (the input's existing `aria-label` and `onChange` are preserved verbatim; only its visibility changes — hide it with the existing `.visuallyHidden`/`sr-only` pattern, or `className={styles.hiddenInput}`).
- "Take a picture": same shape, but the hidden input has `capture="environment"` (mirroring `Capture.tsx:54`), `aria-label="Capture a photo of your target"`, and the button uses `<BsCamera />` + label "Take a picture". The button is wrapped in a `<span className={styles.mobileOnly}>` (or the button itself carries that class) so CSS hides it at >760px.
- Wrap both buttons in `<div className={styles.uploadActions}>` for the horizontal layout.

#### 5.3 Layout + warning CSS

**File**: `src/frontend/src/components/Upload.module.css`

**Intent**: Style the warning callout with the reserved warning tokens, the button group as horizontal-with-vertical-stack-on-mobile, the hidden inputs, and the mobile-only visibility — all at the established 760px breakpoint.

**Contract**:
- `.warning { background: var(--color-warning-bg); border: 1px solid var(--color-warning-border); color: var(--color-warning-text); padding: 0.75rem 1rem; border-radius: 8px; font-size: 0.9rem; margin-bottom: 1rem; text-align: left; }` (first consumer of these tokens).
- `.uploadActions { display: flex; flex-direction: row; gap: 0.75rem; justify-content: center; }` and `@media (max-width: 760px) { .uploadActions { flex-direction: column; } }` (the 760px convention from `Dashboard.module.css:72`).
- `.mobileOnly { display: none; } @media (max-width: 760px) { .mobileOnly { display: inline-flex; } }` — the camera button is hidden on desktop, visible on mobile.
- The custom buttons reuse the `::file-selector-button` styling tokens (`Upload.module.css:19-33`) so they look consistent: `background: var(--color-primary); color: #fff; border: none; border-radius: 8px; padding: 0.6rem 1.25rem; font-size: 0.95rem; font-weight: 500; cursor: pointer;` with `:hover { opacity: 0.9; }`.
- `.hiddenInput { display: none; }` (or the codebase's existing visually-hidden utility if one exists — search for `visually-hidden`/`sr-only` first; if none, `display: none` is fine since the custom button provides the accessible name).
- Decide whether to keep or remove the old `::file-selector-button` rules (`Upload.module.css:19-33`) — if the native input is now `display: none`, those rules are dead and should be removed for cleanliness; the custom button carries the equivalent styling.

### Success Criteria:

#### Automated Verification:

- `make check` passes.
- `make fe-test` passes — existing `Upload` tests (if any) still green; the `onChange` → `handleFile` → `createScoringJob` flow is preserved.
- A new or extended vitest asserts: (a) the warning text renders verbatim, (b) both "Choose file" and "Take a picture" buttons are present, (c) clicking each button calls the corresponding input ref's `.click()` (mock the ref), (d) the `capture="environment"` attribute is present on the camera input and absent on the file input.
- Note: jsdom doesn't honor `capture` or media queries, so the mobile-only visibility is a manual check, not an automated one.

#### Manual Verification:

- Desktop browser (`/upload`): the yellow warning callout shows the verbatim text; the two buttons sit side by side; **only "Choose file" is visible** (camera button hidden at >760px); clicking "Choose file" opens the OS file dialog; selecting a file navigates to `/waiting/:jobId` as before.
- Mobile viewport (DevTools ≤760px, or a real phone): the warning still shows; the two buttons stack vertically; **both "Choose file" and "Take a picture" are visible**; "Take a picture" opens the camera (`capture="environment"`); "Choose file" still opens the file picker.
- `/capture` route still works as before (fallback unchanged).
- The warning callout's yellow palette renders correctly (tokens resolve).

**Implementation Note**: After Phase 5 + automated verification pass, pause for the final manual confirmation (desktop + mobile viewport) before considering the change complete.

---

## Testing Strategy

### Unit Tests:

- `TopBar.test.tsx` (new) — asserts `aria-haspopup="menu"`/`role="menu"` markup, the Logout `onClick` calls `onLogout`, and the trigger is keyboard-focusable.
- `ScoreRow.test.tsx` (extend) — asserts each of the three buttons renders an icon node + text label, and existing `aria-label`s are preserved.
- `DailyAverageChart.test.tsx` (extend or new) — asserts the YAxis `tickFormatter` and Tooltip `formatter` produce 2-decimal strings (unit-test the formatter functions directly, or render with a mock value like `7.3333` and assert `'7.33'`).
- `Upload.test.tsx` (extend or new) — asserts the verbatim warning text, the presence of both buttons, the `capture` attribute on the camera input only, and that each button's click triggers the right input ref.

### Integration Tests:

- No new cross-domain integration tests — all changes are frontend-only and the existing `Dashboard.test.tsx` (matchMedia stub for `useIsMobile`) continues to cover route behavior. The `/capture` fallback route is intentionally untouched, so no integration regression is expected.

### Manual Testing Steps:

1. Log in; hover the nick in the top-right → confirm the Logout menu appears; click Logout → confirm `POST /logout` + reload.
2. Tab to the nick → confirm `:focus-within` reveals the menu; Tab into Logout; Enter → confirm logout.
3. Open `/scores` → confirm each row's three buttons have icons (Preview=target graphic smaller, Modify=pencil, Delete=trash) + text labels.
4. Open home `/` "Recent results" → confirm the same row appearance (propagation check).
5. Open `/` chart → confirm Y-axis ticks read `0.00 … 10.00` and a hovered tooltip reads e.g. `7.33`.
6. Open `/upload` on desktop → confirm the yellow warning callout (verbatim text) and **only** "Choose file"; click it → file dialog → job starts.
7. Open `/upload` at ≤760px width → confirm buttons stack vertically and **both** buttons show; "Take a picture" opens the camera.
8. Navigate to `/capture` directly → confirm the fallback route still works.

## Performance Considerations

- `react-icons` is tree-shakeable per-icon; only `BsPencil`, `BsTrash3`, `BsUpload`, `BsCamera` (and any TopBar icons) land in the bundle — sub-KB impact. No runtime cost.
- The TopBar dropdown is pure CSS (`:hover`/`:focus-within`) — no JS, no event listeners, no re-renders.
- The chart formatting change is two pure functions called per-tick/per-hover — negligible.
- The upload restructure adds one extra hidden `<input>` DOM node — trivial.
- No backend, no new network calls, no state-management change — performance profile is effectively unchanged.

## Migration Notes

- No data migration — all changes are frontend display/UI.
- The TopBar props change (`{ nick }` → `{ nick; onLogout }`) is internal to the React tree; no external API contract changes.
- The `/upload` input restructure changes which DOM element the user clicks but preserves the `onChange` → `handleFile` → `createScoringJob` flow verbatim — no behavioral migration.
- `/capture` is intentionally left in place; no redirect, no removal. Future cleanup can revisit.

## References

- Research: `context/changes/ui-chores/research.md`
- Prior change (dashboard conventions): `context/changes/user-score-dashboard/plan.md` (§3.7 minimal-toolchain stance; `target.svg` immutability), `context/changes/user-score-dashboard/research.md` (760px breakpoint, no icon library, 0–10 scale)
- Codebase edit points (verified): `TopBar.tsx:4-11`, `TopBar.module.css:15-18`, `AppShell.tsx:36-43`, `Sidebar.tsx:51-55`, `App.tsx:52-61`, `api.ts:57-67`, `ScoreRow.tsx:12,37-62`, `ScoreRow.module.css:43-70`, `DailyAverageChart.tsx:50-64`, `Upload.tsx:47-55`, `Upload.module.css:19-33`, `Capture.tsx:50-60`, `styles.css:30-32`, `src/frontend/package.json:17-23`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Add `react-icons` dependency

#### Automated

- [x] 1.1 `react-icons` installed (`package.json` + `package-lock.json` updated), throwaway import sanity-check compiles then removed — 797d725
- [x] 1.2 `make check` passes after install — 797d725

#### Manual

- [x] 1.3 `git diff src/frontend/package.json` shows only the `react-icons` line under `dependencies` — 797d725

### Phase 2: Logout dropdown in TopBar

#### Automated

- [x] 2.1 `onLogout` threaded into `TopBar` at `AppShell.tsx:36` — a0bdeec
- [x] 2.2 `TopBar.tsx` renders accessible disclosure (`aria-haspopup="menu"`, `role="menu"`, Logout `onClick={onLogout}`) — a0bdeec
- [x] 2.3 `TopBar.module.css` adds `.nickGroup`/`.nickTrigger`/`.menu` with `:hover`+`:focus-within` reveal — a0bdeec
- [x] 2.4 `TopBar.test.tsx` asserts markup + Logout handler wiring — a0bdeec
- [x] 2.5 `make check` + `make fe-test` pass — a0bdeec

#### Manual

- [x] 2.6 Desktop: hover nick → menu appears → click Logout → `POST /logout` + reload
- [x] 2.7 Keyboard: Tab to nick → menu reveals → Tab into Logout → Enter → logout
- [x] 2.8 No header layout shift; nick visual position unchanged

### Phase 3: Icon-ify Preview/Modify/Delete buttons

#### Automated

- [x] 3.1 `react-icons/bs` imports (`BsPencil`, `BsTrash3`) added to `ScoreRow.tsx` — 1efd471
- [x] 3.2 Icons rendered inside Modify/Delete; Preview `target.svg` given smaller variant class — 1efd471
- [x] 3.3 `.targetIcon` (smaller) rule added to `ScoreRow.module.css` — 1efd471
- [x] 3.4 `ScoreRow` vitest asserts icon+label per button, `aria-label`s preserved — 1efd471
- [x] 3.5 `make check` + `make fe-test` pass — 1efd471

#### Manual

- [x] 3.6 `/scores`: each row shows Preview (small target graphic)+Modify (pencil)+Delete (trash), each with text label
- [x] 3.7 Home `/` "Recent results": same appearance (propagation check)
- [x] 3.8 Delete icon picks up danger color; buttons remain keyboard-operable; SR announces `aria-label`

### Phase 4: 2-decimal chart formatting

#### Automated

- [x] 4.1 `YAxis` `tickFormatter={(v) => Number(v).toFixed(2)}` added at `DailyAverageChart.tsx:54` — 8db89b5
- [x] 4.2 `Tooltip` `formatter` added at `DailyAverageChart.tsx:55` — 8db89b5
- [x] 4.3 `DailyAverageChart` vitest asserts formatter produces 2-decimal strings — 8db89b5
- [x] 4.4 `make check` + `make fe-test` pass — 8db89b5

#### Manual

- [x] 4.5 Home `/` chart: Y-axis ticks read `0.00 … 10.00`; hovered tooltip reads e.g. `7.33`
- [x] 4.6 Chart layout/spacing unchanged; 2-decimal ticks don't clip (adjust `left` margin if they do)

### Phase 5: PII warning + mobile camera button on `/upload`

#### Automated

- [x] 5.1 PII warning callout added to `Upload.tsx` (verbatim wording, `role="note"`) — d38086a
- [x] 5.2 Upload input restructured into two custom buttons + hidden inputs (Choose file + Take a picture w/ `capture="environment"`) — d38086a
- [x] 5.3 `Upload.module.css` adds `.warning` (warning tokens), `.uploadActions` (row→column at 760px), `.mobileOnly` (hidden >760px), button styling; dead `::file-selector-button` rules removed — d38086a
- [x] 5.4 `Upload` vitest asserts verbatim warning text, both buttons present, `capture` on camera input only, ref-`.click()` wiring — d38086a
- [x] 5.5 `make check` + `make fe-test` pass — d38086a

#### Manual

- [x] 5.6 Desktop `/upload`: yellow callout (verbatim text) shows; only "Choose file" visible; click → file dialog → job starts
- [x] 5.7 Mobile viewport (≤760px): buttons stack vertically; both visible; "Take a picture" opens camera; "Choose file" opens picker
- [x] 5.8 `/capture` fallback route still works
- [x] 5.9 Warning callout's yellow palette resolves correctly
