# GitHub Star button + EU cookie consent popup — Implementation Plan

## Overview

Add three frontend-facing pieces to the Target-o-meter SPA: (1) a GitHub "Star"
button on the unauthenticated `Welcome` header, (2) a server-rendered
`/privacy` stub page documenting the strictly-necessary cookies in use, and
(3) a `vanilla-cookieconsent` v3 GDPR/ePrivacy banner mounted site-wide from
`main.tsx` whose "Cookie Policy" link points at the new `/privacy` page.

The nick→logout dropdown (research "Action 3") is **out of scope** — it will be
a separate change. It shares no code paths with this work.

## Current State Analysis

The SPA has a single auth seam at `src/frontend/src/App.tsx:39` — `Welcome` is
rendered **only** when `!me.authenticated`. Both the Star button and the cookie
banner therefore land naturally inside / around `Welcome` with no extra auth
guards.

Key facts established by research and verified against the live code:

- **Real stack** is plain React 18 + react-router-dom + recharts
  (`src/frontend/package.json:17-22`). There is **no Oval, no Redux, no UI
  library, no icon library** — `AGENTS.md §1`'s "Oval + Redux" claim is stale
  doc drift, out of scope for this change.
- **No shared components** — each component owns a co-located `*.module.css`;
  there is no `<Button>`, `<Icon>`, or `<Dropdown>`. The Star button is
  hand-rolled, mirroring `.loginBtn` (`Welcome.module.css:24-36`).
- **No external-link pattern exists** — `Welcome.tsx`'s Star `<a>` will be the
  first external anchor in the SPA and sets the convention
  (`target="_blank" rel="noopener noreferrer"`).
- **No `VITE_*` env vars consumed anywhere** — per the user's decision, the
  GitHub URL is hardcoded rather than introduced as the first env var.
- **Strictly-necessary cookies only today** — Django session + CSRF
  (`src/target_o_meter/settings.py:223-243`). No analytics, no marketing
  pixels, no `localStorage`/`sessionStorage` usage. `SameSite=Lax` at
  `settings.py:231` is load-bearing for the OIDC callback and must not change.
- **The SPA shell is served by a catch-all** —
  `src/bff/urls.py`'s `re_path(r"^(?!v1/|login|callback|logout|admin/).*$", index)`
  swallows any unknown top-level path and serves `templates/base.html` (which
  boots the SPA). A real `/privacy` page therefore requires a catch-all
  exclusion + an explicit route.
- **Test conventions** — frontend: co-located `*.test.tsx` (vitest +
  @testing-library), e.g. `Welcome.test.tsx`. Backend URL/view: system tests in
  `tests/system/` (e.g. `test_spa_shell.py`) using the `client` fixture +
  `pytest.mark.django_db`.
- **Verification gate** — `make check` (ruff + import-linter + frontend `tsc
  --noEmit`), then `make fe-test` (adds vitest) / `make be-test` (adds backend
  pytest).

### Key Discoveries:

- `src/frontend/src/App.tsx:39-41` — the single auth seam; `Welcome` is
  unauth-only, so neither the Star button nor the popup needs an `authenticated`
  check inside it.
- `src/frontend/src/components/Welcome.tsx:12-15` — the header (brand +
  Login) is the Star-button anchor.
- `src/frontend/src/components/Welcome.module.css:9-36` — `.topBar` uses
  `justify-content: space-between`; adding the Star button needs a right-side
  actions wrapper so Star + Login group together.
- `src/frontend/src/main.tsx:8-11` — SPA entry; the cookie banner mounts here
  (site-wide, around `<App />`).
- `src/bff/urls.py` (catch-all `re_path`) — must exclude `privacy` or the new
  route is shadowed by the SPA shell.
- `templates/base.html` — the SPA shell template; the privacy page must **not**
  extend it (it would boot React and the auth seam would render `Welcome`
  inside `#root`).
- `src/frontend/src/styles.css:16-36` — `:root` palette tokens; vanilla-
  cookieconsent's `--cc-*` overrides land in this same block.
- `src/frontend/src/api.ts:29` — `document.cookie` CSRF read must keep working;
  `csrftoken` is a strictly-necessary cookie the banner cannot gate.

## Desired End State

1. An unauthenticated visitor to `/` sees the GitHub Star button in the
   `Welcome` header, next to Login; clicking it opens
   `https://github.com/krkruk/target-o-meter` in a new tab.
2. Every visitor (authed or not) sees the cookie consent banner once on first
   visit, with Accept and Reject equally prominent; choosing either stores
   consent (timestamped, revision-versioned) and dismisses the banner. A
   persistent "Cookie settings" control re-opens it.
3. The banner's "Cookie Policy" link navigates to `/privacy`, a real
   server-rendered page listing the strictly-necessary cookies (Django session
   + CSRF) and stating that no analytics/marketing cookies are used.
4. `csrftoken` / `sessionid` continue to work unchanged; the OIDC login flow is
   unaffected.

### How to verify

- `make check` is green (ruff, import-linter, frontend `tsc --noEmit`).
- `make fe-test` is green (existing + new vitest assertions).
- `make be-test` is green (new `/privacy` system test).
- Manual: first-visit banner appears, stores consent, re-opens via "Cookie
  settings"; Star button opens the repo; `/privacy` renders standalone.

## What We're NOT Doing

- **Nick → logout dropdown** (research "Action 3") — separate change.
- **Analytics / marketing cookie categories** — the banner exposes the
  *necessary* category only. When something actually tracks, add the category
  then (and bump the banner `revision` so stored consent re-prompts).
- **`VITE_GITHUB_URL` env var** — URL is hardcoded per the user's decision.
- **Fixing `AGENTS.md §1` stack drift** (Oval/Redux claim) — flagged for a
  separate doc fix; research noted it in Open Questions.
- **Any backend data-model / async / domain-logic change** — purely frontend +
  one BFF view + one template.
- **Authoring a full legal privacy policy** — the stub page states the
  technically-accurate cookie inventory; it is not legal review.

## Implementation Approach

Three phases, ordered so Phase 2's `/privacy` page exists before Phase 3's
banner links to it. Phase 1 (Star button) is independent and lands first as a
quick, low-risk win that also establishes the external-link convention Phase 3
does not need but the codebase benefits from.

- **Phase 1** edits `Welcome.tsx` + `Welcome.module.css` + `Welcome.test.tsx`
  only.
- **Phase 2** adds a standalone Django template, a `privacy` view in
  `src/bff/views.py`, a URL route + catch-all exclusion in `src/bff/urls.py`,
  and a system test.
- **Phase 3** adds the `vanilla-cookieconsent` dependency, a thin wrapper
  module, a `useEffect` mount in `main.tsx`, and `--cc-*` theme overrides in
  `styles.css`.

## Critical Implementation Details

- **Catch-all exclusion is load-bearing (Phase 2).** `src/bff/urls.py`'s
  `re_path(r"^(?!v1/|login|callback|logout|admin/).*$", index)` currently
  serves the SPA shell for `/privacy`. Add `privacy` to the negative lookahead
  AND add an explicit `path("privacy", privacy, name="privacy")` before the
  catch-all. Without the exclusion, the banner's policy link resolves to the
  SPA shell (which renders `Welcome` for unauthed visitors — a dead end).
- **Do not route the policy page through the SPA.** The privacy page is a
  standalone `templates/privacy.html` that does **not** extend `base.html` and
  does **not** mount React. Extending `base.html` would boot the SPA, and the
  auth seam at `App.tsx:39` would render `Welcome` inside `#root` for
  unauthenticated visitors — hiding the policy text.
- **Do not touch `SESSION_COOKIE_SAMESITE`** (`settings.py:231`) or insert any
  consent logic between the OIDC redirect and the session cookie. The cookie
  banner only manages *non-necessary* scripts (none exist yet); it must not
  gate `csrftoken` or `sessionid`. Prior research
  (`context/archive/2026-07-25-sign-in-empty-dashboard/research.md`) flagged
  this chain as fragile.
- **vanilla-cookieconsent owns its own DOM portal.** It is not a React
  component — init via `CookieConsent.run(config)` inside a `useEffect`, call
  `.destroy()` on cleanup. Do not render it as JSX children. The CSS is
  imported once in the wrapper module so Vite bundles it.

## Phase 1: GitHub Star button

### Overview

Add an external GitHub "Star" anchor to the `Welcome` header, immediately left
of the Login button, with an inline SVG star glyph. Establishes the project's
first external-link convention.

### Changes Required:

#### 1. Star button in the Welcome header

**File**: `src/frontend/src/components/Welcome.tsx`

**Intent**: Surface the repo's GitHub URL to unauthenticated visitors as a
"Star" call-to-action next to Login, so the landing page carries social proof
without a new dependency.

**Contract**: Add an `<a>` element to the existing `<header className={styles.topBar}>`
(`Welcome.tsx:12-15`), placed visually left of the Login `<button>`. The anchor
hardcodes `href="https://github.com/krkruk/target-o-meter"`,
`target="_blank"`, and `rel="noopener noreferrer"` — this is the project's
first external link and sets the form every future external anchor should
follow. The anchor contains an inline `<svg>` star glyph plus an
accessible name (e.g. `aria-label="Star Target-o-meter on GitHub"`). Wrap the
Star anchor + Login button in a single right-side flex container (new
`.headerActions` class) so `justify-content: space-between` on `.topBar` still
puts brand-left / actions-right.

#### 2. Star button styles

**File**: `src/frontend/src/components/Welcome.module.css`

**Intent**: Style the Star anchor to read as a secondary action beside the
Login button, matching the existing outlined-button aesthetic.

**Contract**: Add `.starBtn` mirroring `.loginBtn` (`Welcome.module.css:24-36`:
`padding`, `1px solid #1a1a1a`, `background: transparent`, `border-radius: 6px`,
hover inverts to filled). Style the inline SVG to inherit `currentColor` so the
hover inversion recolors the glyph without a separate rule. Add
`.headerActions { display: flex; align-items: center; gap: 0.5rem; }` to group
Star + Login. The existing `@media (max-width: 760px)` block
(`Welcome.module.css:91-96`) may need the button padding/gap tightened so both
controls fit on narrow viewports.

#### 3. Welcome test coverage

**File**: `src/frontend/src/components/Welcome.test.tsx`

**Intent**: Pin the Star button's presence, target URL, and safe external-link
attributes so a regression is caught before the banner work layers on top.

**Contract**: Add assertions to the existing `describe('Welcome')` block: the
Star link is reachable via `screen.getByRole('link', { name: /star.*github/i })`,
its `href` is `https://github.com/krkruk/target-o-meter`, and it has
`target="_blank"` and `rel="noopener noreferrer"`. Do not remove or relax the
existing Login/CTA assertions.

### Success Criteria:

#### Automated Verification:

- `make check` passes (frontend `tsc --noEmit` catches any type error in the
  new JSX/SVG).
- `make fe-test` passes — the new Star-link assertions in `Welcome.test.tsx`
  are green alongside the existing Welcome tests.

#### Manual Verification:

- Visit `/` unauthenticated; confirm the Star button renders in the header
  left of Login, aligned and sized consistently with Login.
- Click the Star button; confirm it opens
  `https://github.com/krkruk/target-o-meter` in a new tab.
- Resize to ~375px width; confirm Star + Login both remain visible and
  legible (no overflow/clipping).
- Run `npm run lint` and `npm run build` inside `src/frontend/` to confirm the
  inline SVG and new CSS compile cleanly under the Vite build.

**Implementation Note**: After completing this phase and all automated
verification passes, pause here for manual confirmation from the human that the
manual testing was successful before proceeding to the next phase.

---

## Phase 2: Privacy / cookie policy stub page

### Overview

Add a standalone server-rendered page at `/privacy` that documents the
strictly-necessary cookies in use, so the cookie banner's "Cookie Policy" link
resolves to a real page. Lands before Phase 3 so the banner's link is never
dead.

### Changes Required:

#### 1. Privacy page template

**File**: `templates/privacy.html` (new)

**Intent**: Give visitors a readable explanation of which cookies the site uses
and why — the legally-relevant target the banner links to.

**Contract**: A self-contained HTML5 document (`<!DOCTYPE html>` … `<html>` …)
that does **not** extend `base.html` and does **not** include the
`{% vite_asset 'src/main.tsx' %}` tag (no SPA boot on this page). Inlines a
small `<style>` block reusing the palette values from `styles.css:16-36`
(`--color-bg: #f7f7f5`, `--color-fg: #1a1a1a`, `--color-border: #e3e1dc`) for
visual consistency. Content states: (a) the site uses two strictly-necessary
cookies — `sessionid` (Django session, HttpOnly) and `csrftoken` (CSRF
protection) — both required for login/security and not disableable; (b) no
analytics or marketing cookies are currently used; (c) a link back to `/`. The
`<title>` is "Cookie Policy — Target-o-meter".

#### 2. Privacy view

**File**: `src/bff/views.py`

**Intent**: Serve the privacy template at a stable URL.

**Contract**: Add a `privacy(request: HttpRequest) -> HttpResponse` function
that `render(request, "privacy.html")`. Mirror the existing `index` view's
signature and import style (`src/bff/views.py:18-20`). Keep it public — no
auth decorator — the policy must be reachable by unauthenticated visitors
(that is the whole point).

#### 3. URL route + catch-all exclusion

**File**: `src/bff/urls.py`

**Intent**: Make `/privacy` resolve to the new view instead of being swallowed
by the SPA-shell catch-all.

**Contract**: Add `from src.bff.views import index, privacy` to the imports,
and `path("privacy", privacy, name="privacy")` to `urlpatterns` alongside the
other explicit `path(...)` entries (before the `re_path` catch-all). **Also**
add `privacy` to the catch-all's negative lookahead so the exclusion is
self-documenting and defensive: the pattern becomes
`r"^(?!v1/|login|callback|logout|admin/|privacy).*$"`. Update the comment
above the `re_path` to mention `privacy` alongside the existing exclusions.

#### 4. Privacy page system test

**File**: `tests/system/test_privacy_page.py` (new)

**Intent**: Pin that `/privacy` returns the standalone page (not the SPA shell)
and contains the cookie inventory.

**Contract**: Mirror `tests/system/test_spa_shell.py`'s style —
`pytestmark = [pytest.mark.django_db, pytest.mark.dev]`, use the `client`
fixture. Assertions: `client.get("/privacy")` returns 200; the body contains
the cookie names (`sessionid`, `csrftoken`) and a "no analytics" statement; and
— critically — the body does **not** contain `<div id="root">` (which would
prove the SPA shell was served instead). Optionally assert the body does not
contain `src/main.tsx` for the same reason.

### Success Criteria:

#### Automated Verification:

- `make check` passes (ruff + import-linter — the new view import satisfies the
  architecture contract since `views.py` already imports into `urls.py`).
- `make be-test` passes — `tests/system/test_privacy_page.py` is green,
  including the "not the SPA shell" assertions.

#### Manual Verification:

- Visit `/privacy` in a browser; confirm it renders the standalone page (no
  React boot, no `Welcome` content) with the cookie inventory and a link back
  to `/`.
- Visit `/privacy` while logged in; confirm the same page renders (the route is
  public, no auth gate).
- Confirm `/privacy-typos` still 404s (the catch-all exclusion is specific to
  `privacy`, not a prefix).

**Implementation Note**: After completing this phase and all automated
verification passes, pause here for manual confirmation from the human that the
manual testing was successful before proceeding to the next phase.

---

## Phase 3: Cookie consent popup (vanilla-cookieconsent v3)

### Overview

Add a GDPR/ePrivacy consent banner using `vanilla-cookieconsent` v3, configured
with the *necessary* category only and the `/privacy` page as the policy link,
mounted site-wide from `main.tsx`.

### Changes Required:

#### 1. Add the dependency

**File**: `src/frontend/package.json`

**Intent**: Bring in the consent library.

**Contract**: Add `"vanilla-cookieconsent": "^3.1.0"` to `dependencies`
(alongside `react`, `react-router-dom`, `recharts`). The package ships built-in
TypeScript declarations and 0 runtime dependencies. Run `npm install` in
`src/frontend/` so `package-lock.json` updates; commit both files.

#### 2. Consent wrapper module

**File**: `src/frontend/src/cookieConsent.ts` (new)

**Intent**: Encapsulate the library's configuration and lifecycle so `main.tsx`
stays a thin entry point and the consent config is auditable in one place.

**Contract**: Export an `initCookieConsent(): () => void` function (or
equivalent) that:
- imports the library's CSS once (`import "vanilla-cookieconsent/dist/cookieconsent.css"`);
- calls `CookieConsent.run({...})` with a config object containing:
  - `revision: 1` (bump to re-prompt on future policy change),
  - one category `necessary` marked `enabled: true, readOnly: true` (covers
    `sessionid` + `csrftoken`),
  - the GUI strings (title, description, Accept/Reject/Save buttons) with
    Accept and Reject equally prominent (no dark pattern),
  - `privacyPolicyUrl: "/privacy"` (resolves via the Phase 2 route),
  - the per-category consent store on by default;
- returns a cleanup function that calls `CookieConsent.destroy()`.
- Use `window.location.origin + "/privacy"` or just `"/privacy"` per the
  library's expectation for same-origin policy links.

Do **not** register any analytics/marketing category — per the user's decision,
*necessary only* is exposed. When a tracking service lands later, add its
category then and bump `revision`.

#### 3. Mount the banner site-wide

**File**: `src/frontend/src/main.tsx`

**Intent**: Run the consent banner exactly once for the whole SPA, decoupled
from the auth seam.

**Contract**: Inside the existing `if (rootEl) { … }` block (`main.tsx:8-11`),
call `initCookieConsent()` in a `useEffect` so the banner initializes after the
root mounts, and wire the returned cleanup into the effect's teardown. The
banner's DOM portal is owned by the library (appended to `document.body`), so
it renders above whatever branch the auth seam takes (`Welcome` or `AppShell`).
Do not pass the consent state into `<App />` — the library is self-contained.

A `useEffect` in `main.tsx` requires importing `useEffect` from `react`; if
keeping `main.tsx` effect-free is preferred, a tiny `<CookieConsentProvider />`
component rendered as a sibling of `<App />` inside `createRoot(...).render(...)`
is the alternative — pick whichever reads cleaner, the contract is "init once,
destroy on unmount".

#### 4. Theme overrides

**File**: `src/frontend/src/styles.css`

**Intent**: Make the banner match the app's palette instead of the library
default.

**Contract**: Add `--cc-*` overrides inside the existing `:root` block
(`styles.css:16-36`) so vanilla-cookieconsent picks up the app's tokens. At
minimum: `--cc-bg`, `--cc-text-color`, `--cc-btn-primary-bg`,
`--cc-btn-primary-color`, `--cc-btn-secondary-bg`, `--cc-btn-border-color`,
mapping to the existing `--color-bg` / `--color-fg` / `--color-primary` /
`--color-border` values. Reference the library's theming docs for the full
variable list.

#### 5. Consent wrapper test (configuration smoke test)

**File**: `src/frontend/src/cookieConsent.test.ts` (new)

**Intent**: Guard that the wrapper exports the expected shape and that the
config points at `/privacy`, so a future edit can't silently break the policy
link or drop the `necessary`-only invariant.

**Contract**: A lightweight vitest test that imports the wrapper module and
asserts the exported init function exists and returns a cleanup (function).
Because vanilla-cookieconsent manipulates `document.body` and reads
`localStorage`, do **not** attempt to assert on rendered banner DOM in jsdom —
that is brittle and covered by manual verification. If the config is extracted
as a named export (recommended), assert on `privacyPolicyUrl === "/privacy"`
and that the `necessary` category is the only non-internal category declared.

### Success Criteria:

#### Automated Verification:

- `make check` passes (`tsc --noEmit` — the new module type-checks against the
  library's bundled declarations; ruff/import-linter unaffected).
- `make fe-test` passes — `cookieConsent.test.ts` is green and no existing
  frontend test regressed (the banner's body portal must not interfere with
  `Welcome.test.tsx` / `AppShell.test.tsx` renders).

#### Manual Verification:

- Clear site data; visit `/` unauthenticated; confirm the consent banner
  appears with Accept and Reject equally prominent and a "Cookie Policy" link.
- Click "Cookie Policy"; confirm it navigates to `/privacy` (the Phase 2 page).
- Click "Reject all"; confirm the banner dismisses and `localStorage` (or the
  library's chosen store) holds a consent record.
- Reload; confirm the banner does **not** reappear (consent persisted).
- Click the persistent "Cookie settings" control (gear icon or floating
  button, depending on config); confirm the banner re-opens.
- Complete the OIDC login flow end-to-end; confirm login still works
  (`SameSite=Lax` session cookie unaffected) and `csrftoken` is still readable
  by `api.ts` (e.g. a PATCH that previously worked still works).
- Visit the site logged-in; confirm the same banner behavior (site-wide mount).

**Implementation Note**: After completing this phase and all automated
verification passes, pause here for manual confirmation from the human that the
manual testing was successful before considering the change complete.

---

## Testing Strategy

### Unit Tests:

- `Welcome.test.tsx` — Star link presence, href, `target`, `rel` (Phase 1).
- `cookieConsent.test.ts` — wrapper export shape, `privacyPolicyUrl`,
  necessary-only category invariant (Phase 3).

### Integration / System Tests:

- `tests/system/test_privacy_page.py` — `/privacy` returns 200, renders the
  standalone page (not the SPA shell), contains the cookie inventory (Phase 2).

### Manual Testing Steps:

1. Unauthenticated `/`: Star button visible, opens repo in new tab.
2. `/privacy`: standalone page renders (no React boot), lists `sessionid` +
   `csrftoken`, states no analytics.
3. `/privacy-typos`: 404 (catch-all exclusion is specific).
4. First visit: banner appears, Accept and Reject equally prominent.
5. Banner "Cookie Policy" link → `/privacy`.
6. Reject → banner dismisses, consent stored, no re-prompt on reload.
7. "Cookie settings" re-opens the banner.
8. Full OIDC login still works; an authenticated PATCH still carries CSRF.

## Performance Considerations

- `vanilla-cookieconsent` is ~30 KB JS+CSS with 0 dependencies — negligible
  against the existing React + recharts bundle. The CSS is imported once and
  Vite-tree-shakes/hashes it.
- The banner initializes once in `main.tsx`; it does not re-render per route
  change (the library manages its own DOM).
- The `/privacy` page is a static server-rendered template with no JS bundle —
  it loads faster than any SPA route.

## Migration Notes

- No database migration — no models touched.
- No data migration — the consent record lives in the browser's `localStorage`
  (managed by the library), not the DB.
- Rollback is trivial: revert the commits; the banner disappears and the
  `localStorage` key becomes orphaned (harmless). The `/privacy` route can stay
  even if the banner is rolled back — it's a useful standalone page.

## References

- Research: `context/changes/add-github-button-and-cookies/research.md`
- Library docs: <https://cookieconsent.orestbida.com>
- npm: <https://www.npmjs.com/package/vanilla-cookieconsent> (v3.1.0)
- Auth seam: `src/frontend/src/App.tsx:39-41`
- Welcome header anchor: `src/frontend/src/components/Welcome.tsx:12-15`
- Button style to mirror: `src/frontend/src/components/Welcome.module.css:24-36`
- SPA entry / banner mount: `src/frontend/src/main.tsx:8-11`
- Catch-all to exclude: `src/bff/urls.py` (`re_path(r"^(?!v1/|login|callback|logout|admin/).*$", index)`)
- Palette tokens: `src/frontend/src/styles.css:16-36`
- Cookie settings (load-bearing): `src/target_o_meter/settings.py:223-243` (`SameSite=Lax` at 231)
- System-test pattern: `tests/system/test_spa_shell.py`
- Fragile OIDC chain prior: `context/archive/2026-07-25-sign-in-empty-dashboard/research.md`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: GitHub Star button

#### Automated

- [x] 1.1 `make check` passes (frontend `tsc --noEmit`) — d4fe186
- [x] 1.2 `make fe-test` passes — Star-link assertions in `Welcome.test.tsx` green — d4fe186

#### Manual

- [x] 1.3 Star button renders in Welcome header left of Login, aligned with Login — d4fe186
- [x] 1.4 Clicking Star opens `https://github.com/krkruk/target-o-meter` in a new tab — d4fe186
- [x] 1.5 At ~375px width, Star + Login both remain visible (no overflow/clipping) — d4fe186

### Phase 2: Privacy / cookie policy stub page

#### Automated

- [x] 2.1 `make check` passes (ruff + import-linter accept the new view import) — 82a9133
- [x] 2.2 `make be-test` passes — `tests/system/test_privacy_page.py` green (incl. "not the SPA shell" assertions) — 82a9133

#### Manual

- [x] 2.3 `/privacy` renders the standalone page (no React boot, no `Welcome` content) — 82a9133
- [x] 2.4 `/privacy` lists `sessionid` + `csrftoken` and states no analytics/marketing — 82a9133
- [x] 2.5 `/privacy` reachable + identical while logged in (public route) — 82a9133
- [x] 2.6 `/privacy-typos` returns 404 (catch-all exclusion is specific) — 82a9133

### Phase 3: Cookie consent popup

#### Automated

- [x] 3.1 `make check` passes (`tsc --noEmit` against the library's bundled declarations) — 38ed2d2
- [x] 3.2 `make fe-test` passes — `cookieConsent.test.ts` green, no existing frontend test regressed — 38ed2d2

#### Manual

- [x] 3.3 First visit (site data cleared): banner appears, Accept and Reject equally prominent — 38ed2d2
- [x] 3.4 Banner "Cookie Policy" link navigates to `/privacy` — 38ed2d2
- [x] 3.5 Reject → banner dismisses, consent stored, no re-prompt on reload — 38ed2d2
- [x] 3.6 "Cookie settings" control re-opens the banner — 38ed2d2 — superseded by d51de0c: the persistent "Cookie settings" button was removed per request (took screen real estate); the banner now re-opens only via the library's `CookieConsent.show()` API, not a UI control. No E2E row for 3.6 (no UI path to drive).
- [x] 3.7 Full OIDC login still works (SameSite=Lax session unaffected; `csrftoken` still readable by `api.ts`) — 38ed2d2
- [x] 3.8 Banner behaves the same on authenticated pages (site-wide mount) — 38ed2d2

#### E2E (browser-level, added post-implementation)

> Driven by `/10x-e2e` after the plan was marked implemented. These close the
> browser-level banner risks that were manual-only (the banner is a
> library-owned DOM portal appended to `document.body`; consent persists in the
> `cc_cookie` cookie across a real reload; the policy link performs real
> SPA→Django navigation). jsdom cannot reach any of these
> (`cookieConsent.test.ts:8-10` defers banner DOM to manual verification).
>
> Note: vanilla-cookieconsent v3.1.0 ships `hideFromBots: true` and suppresses
> the banner when `navigator.webdriver === true` (every Playwright browser);
> the spec masks `navigator.webdriver` (test-scoped, production config
> untouched). Each test was break-verified: inverting the protected behavior
> turned the test red on the protecting assertion.

- [x] 3.9 E2E: first-visit banner appears (Accept + Reject equally prominent) and "Cookie Policy" link navigates to the standalone `/privacy` page (not the SPA shell) — SHA-pending
- [x] 3.10 E2E: Reject dismisses the banner and consent persists in `cc_cookie` across a reload (no re-prompt) — SHA-pending
- [x] 3.11 E2E: banner mounts site-wide on the authenticated dashboard without breaking the SPA boot — SHA-pending
