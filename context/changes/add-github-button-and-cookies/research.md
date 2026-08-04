---
date: 2026-08-04T15:45:15+02:00
researcher: opencode
git_commit: c0b2f289a1d670f22f8b3b84e6352bf4618ea937
branch: master
repository: krkruk/target-o-meter
topic: "Add GitHub Star button + EU cookie consent popup on the unauthenticated landing page (and document the nick→logout dropdown relocation)"
tags: [research, codebase, frontend, welcome, topbar, sidebar, cookie-consent, gdpr, github-button]
status: complete
last_updated: 2026-08-04
last_updated_by: opencode
---

# Research: GitHub Star button + EU cookie popup (unauthenticated) + nick/logout dropdown notes

**Date**: 2026-08-04T15:45:15+02:00
**Researcher**: opencode
**Git Commit**: `c0b2f289a1d670f22f8b3b84e6352bf4618ea937`
**Branch**: `master`
**Repository**: [krkruk/target-o-meter](https://github.com/krkruk/target-o-meter)

## Research Question

Investigate three UI actions and produce concrete anchors for planning:

1. **Add a GitHub "Star" button on the main page**, visible to unauthenticated users.
2. **Add an EU-regulation (GDPR/ePrivacy) cookie consent popup**, starting on the unauthenticated page.
3. **(Documentation only — not researched in depth)** Move the logout link into the top-right nickname, turning the nickname into a dropdown that contains a Logout action.

Per the user's scoping answers: **depth = quick overview**, **cookie popup = recommend a library**. Actions 1 and 2 are the research focus; action 3 is recorded here only so the plan phase has the anchors.

## Summary

- **Stack correction up front:** `AGENTS.md §1` describes the frontend as "React + Oval + Redux". The *actual* installed stack ([`src/frontend/package.json`](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/package.json)) is **plain React 18 + react-router-dom + recharts**, with **no Oval, no Redux, no UI library, no icon library**. State is plain `useState` in `App.tsx`. Plan against the real stack, not the doc.
- **Single auth seam.** Unauthenticated vs authenticated is decided in exactly one place — `App.tsx:39`. The unauthenticated landing page is the `Welcome` component, rendered *only* when `!me.authenticated`. Both Action 1 and Action 2 belong inside (or alongside) `Welcome`; no extra auth guard is needed there.
- **GitHub repo URL is not exposed to the SPA today.** The repo is unambiguously `https://github.com/krkruk/target-o-meter` (from deployment config), but no env var / config knob surfaces it to the client. The plan must decide: hardcode vs introduce a `VITE_GITHUB_URL` (no precedent for either).
- **No existing external-link pattern.** The Star button will be the first `<a href="https://…">` in the SPA; the plan should set the convention (`target="_blank" rel="noopener noreferrer"`).
- **No icon library.** The Star glyph must be an inline `<svg>` JSX fragment (no precedent) or a new SVG asset imported like the existing `target.svg`.
- **Nothing tracks today.** No analytics, no marketing pixels — only strictly-necessary Django session + CSRF cookies. The cookie popup is a forward-looking compliance artifact; the gating scope right now is essentially zero, but the chosen library's consent *store* matters for when analytics lands later.
- **Recommended cookie library: [`vanilla-cookieconsent`](https://github.com/orestbida/cookieconsent) v3** (framework-agnostic, strongest EU/GDPR story, themeable via the same CSS-custom-property approach the SPA already uses).

## Detailed Findings

### 1. The unauthenticated landing page (`Welcome`) — anchor for Actions 1 & 2

The SPA's only unauthenticated surface is `Welcome`. It is rendered directly by the auth seam:

- [src/frontend/src/App.tsx:39-41](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/App.tsx#L39-L41):
  ```tsx
  if (!me.authenticated) {
    return <Welcome onLogin={login} />;
  }
  ```

`Welcome` is small (29 lines). The header already places brand-left / Login-right and is the natural home for the Star button:

- [src/frontend/src/components/Welcome.tsx:9-28](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/components/Welcome.tsx#L9-L28):
  ```tsx
  <div className={styles.page}>
    <header className={styles.topBar} role="banner">
      <span className={styles.brand}>Target-o-meter</span>
      <button className={styles.loginBtn} onClick={onLogin}>Login</button>
    </header>
    <main className={styles.hero} role="region" aria-label="hero">
      …
      <button className={styles.cta} onClick={onLogin}>Get started</button>
    </main>
  </div>
  ```

Because `Welcome` is only ever mounted for unauthenticated visitors, **neither the Star button nor the cookie popup needs an `authenticated` check inside it.** Both can be placed there directly.

### 2. GitHub repo URL — not exposed to the SPA (open decision)

Repo-wide search found **no** env var or config the SPA can read:

- No `GITHUB_*` / `REPO_URL` / `STAR_URL` in `.env` (only `GOOGLE_API_KEY`, `AUTH0_*`, `APP_BASE_URL`, `OWNER_SUB_ID`, dev-bypass vars).
- No `import.meta.env` / `VITE_*` consumed anywhere in `src/frontend/src/`.

The repo identity is, however, unambiguous in deployment config:

- [.railway/railway.ts:141](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/.railway/railway.ts#L141) — `source: github("krkruk/target-o-meter", { branch: "master" })`
- [context/deployment/deploy-plan.md:95](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/context/deployment/deploy-plan.md#L95) — `git remote add origin git@github.com:krkruk/target-o-meter.git`

**Effective URL for the Star button:** `https://github.com/krkruk/target-o-meter`. The plan must decide how the SPA receives it — hardcode the literal, or introduce the project's first `VITE_GITHUB_URL` env var (there is no precedent to follow).

### 3. Styling, buttons, icons — conventions to match

- **Styling system:** CSS Modules + CSS custom properties. Explicitly stated at [src/frontend/src/styles.css:1-8](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/styles.css#L1-L8): *"No Tailwind, no CSS-in-JS …"*.
- **Palette tokens** at [styles.css:16-36](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/styles.css#L16-L36): `--color-primary: #1a1a1a`, `--color-bg`, `--color-danger`, etc.
- **Per-component convention:** every component has a co-located `*.module.css` imported as `import styles from './X.module.css'`. There is **no shared Button component** — buttons are plain `<button>` elements with a class.
- **Closest button pattern to mirror** for the Star button — [Welcome.tsx:14](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/components/Welcome.tsx#L14) + [Welcome.module.css:24-36](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/components/Welcome.module.css#L24-L36):
  ```css
  .loginBtn {
    padding: 0.4rem 1rem;
    border: 1px solid #1a1a1a;
    background: transparent;
    border-radius: 6px;
    font: inherit;
    cursor: pointer;
  }
  .loginBtn:hover { background: #1a1a1a; color: #fff; }
  ```
  (A filled-CTA variant `.cta` also exists at [Welcome.module.css:68-82](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/components/Welcome.module.css#L68-L82).)
- **No icon library** (no `lucide`, `heroicons`, `react-icons`). Existing "icons" are Unicode glyphs (`⌂`, `⚙`, `⏻` in [Sidebar.tsx](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/components/Sidebar.tsx)) or a single standalone asset `assets/target.svg` rendered via `<img>`. The Star glyph must be an inline `<svg>` JSX fragment or a new SVG asset — do **not** introduce an icon dependency.

### 4. External-link convention — none yet (Action 1 establishes it)

Grepping `src/frontend` for `href=`, `<a`, `target=`, `rel=` returns zero real matches. All in-app navigation uses react-router's `<Link>`; the only full-page nav is `window.location.href = '/login'` (internal, [api.ts:75](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/api.ts#L75)). The Star button is the **first** external anchor; the plan should fix the form:

```tsx
<a href="https://github.com/krkruk/target-o-meter" target="_blank" rel="noopener noreferrer">
```

### 5. Cookie / tracking usage today — none non-essential

**Backend (strictly-necessary cookies only — ePrivacy-exempt):**

- [src/target_o_meter/settings.py:223-243](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/target_o_meter/settings.py#L223-L243): `SESSION_COOKIE_*` (HttpOnly, Secure-gated, `SameSite=Lax`, 2h age) and `CSRF_*` (`SameSite=Lax`, `HttpOnly=False` so the SPA can read it).
- ⚠️ `SESSION_COOKIE_SAMESITE = "Lax"` ([settings.py:231](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/target_o_meter/settings.py#L231)) is **load-bearing for the OIDC callback** — do not change it.

**Frontend — CSRF read only:**

- [src/frontend/src/api.ts:29](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/api.ts#L29): `const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);` — reads Django's CSRF token for PATCH/POST. Must keep working; list `csrftoken` under the popup's *necessary* (non-disableable) category.

**Confirmed absent:** no `gtag`/`gtm`/`GA`/`fbq`/`hotjar`/`plausible`/`matomo`/`dataLayer`, no `<script>` analytics in `templates/base.html` or `src/frontend/index.html`. No `localStorage`/`sessionStorage` usage in source.

**Implication:** There is nothing to gate today. The popup's value is its *consent store* + forward compatibility — when analytics lands later, scripts load only after opt-in.

### 6. Cookie popup mount point

Mount hierarchy: `templates/base.html:25` → `<div id="root">` → [main.tsx:8-11](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/main.tsx#L8-L11) → `<App />`. The auth seam in [App.tsx:31-49](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/App.tsx#L31-L49) returns three branches: Loading…, `<Welcome>` (unauth), `<BrowserRouter><AppShell/></BrowserRouter>` (auth).

Because the user wants the popup to **start** on the unauthenticated page, the simplest placement is inside (or as a sibling of) `Welcome`. If wider coverage is later wanted, mount it once in `main.tsx` around `<App />` (vanilla-cookieconsent owns its own DOM portal, so a single mount there is cleanest and decoupled from the auth seam).

### 7. Library recommendation — `vanilla-cookieconsent` v3

Comparison of the realistic React + Vite candidates (EU/GDPR focus):

| Criterion | **vanilla-cookieconsent** v3 (orestbida) | **klaro** (KIProtect) | **react-cookie-consent** (Mastermindzh) | **cookieconsent** (osano) |
|---|---|---|---|---|
| Size | ~30 KB JS+CSS, no deps | ~50 KB+ (full UI + manager) | ~5 KB, trivial | a few KB |
| Consent store | First-class: per-category, timestamped, `revision`-versioned, re-prompts on change | First-class, app-managed | Minimal ("accepted" flag) | Minimal |
| Granular categories | Excellent — `categories` + per-service mapping, auto-disable until consent | Excellent | Poor (accept-all only) | Limited |
| A11y | Strong (ARIA, keyboard trap, focus mgmt, `prefers-reduced-motion`) | Decent | Basic | Basic |
| React-friendliness | Framework-agnostic; thin `useEffect` wrapper (~30 lines) | Framework-agnostic; same | Native React component | Framework-agnostic |
| Script-loader gating | Built-in — services declare category; injected only after consent | Built-in via its API | None — banner only | None |
| Maintenance | Very active (2024–25) | Maintained, slower cycle | Lightly maintained | **DEPRECATED** (Osano → paid CMP; repo archived) |
| License | MIT | MIT | MIT | MIT |
| EU/GDPR fit | Strongest (designed around EU/UK GDPR + ePrivacy) | Strong | Weak | n/a |

**Recommendation: [`vanilla-cookieconsent`](https://github.com/orestbida/cookieconsent) v3.**

Reasoning:

1. **Best EU/GDPR story.** The consent store (timestamped, per-category, revokable, re-prompt on revision) is the core feature, not an afterthought. The "Reject all" button is first-class and as prominent as "Accept all" — matching recent CNIL / EDPB guidance.
2. **Forward-looking.** Nothing tracks today, but when analytics lands you declare services under `categories` and the loader gate is already wired — no refactor.
3. **Framework-agnostic = future-proof.** A ~30-line `useEffect` wrapper in `main.tsx` is all React needs; if Oval/Redux lands later (per `AGENTS.md §1`), the consent layer is untouched.
4. **Theming matches the stack.** vanilla-cookieconsent exposes its palette as `--cc-bg`, `--cc-btn-primary-bg`, etc. — override them in the same `:root` ([styles.css:16-36](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/styles.css#L16-L36)) you already use. No Tailwind/CSS-in-JS mismatch.

**Stack-specific caveats:**

- It is **not** a React component — init in `useEffect`, call `.destroy()` on unmount. Don't try to render it as JSX children; it owns its own DOM portal.
- `csrftoken` ([api.ts:29](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/api.ts#L29)) must keep working — put it in the *necessary* category (cannot be disabled).
- Don't insert any consent logic between the OIDC redirect and the `SameSite=Lax` session cookie. vanilla-cookieconsent only manages *non-necessary* scripts, so this is fine — just don't get creative. (Prior context: [context/archive/2026-07-25-sign-in-empty-dashboard/research.md](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/context/archive/2026-07-25-sign-in-empty-dashboard/research.md) flagged this chain as fragile.)
- Do **not** pick `react-cookie-consent` just because it's the easiest React install — its lack of real granular categories and absent script-gating means you'd replace it the moment analytics lands. `osano/cookieconsent` is deprecated and should not be considered for a new build.

### 8. What "matches EU regulation" requires of the banner

(EDPB Guidelines 03/2020; ePrivacy art. 5(3); CNIL guidance.) The banner must:

1. Make the **"Reject all" button as visually prominent** as "Accept all" — no dark patterns, no pre-ticked boxes.
2. Offer **granular per-category consent** — at minimum *necessary / analytics / marketing* — each non-necessary category independently toggleable.
3. **Store consent before any non-necessary script loads** — load gated on the stored answer, not load-then-remove.
4. Keep consent **revokable** at any time via a persistent "Cookie settings" control (not a one-shot banner).
5. **Persist the choice with timestamp and version**, so a policy change can re-prompt.

Of the candidates above, **vanilla-cookieconsent v3 enforces all five by construction** (categories, `autoClearCookies`, `revision`, equal-weight Accept/Reject). `react-cookie-consent` fails (3) and (4); `osano/cookieconsent` is deprecated.

## Action 3 (documentation only) — nick → logout dropdown

Recorded for the plan phase; not researched in depth per the user's instruction.

- **Nickname today** — rendered in `TopBar`: [TopBar.tsx:1-11](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/components/TopBar.tsx#L1-L11), specifically [TopBar.tsx:8](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/components/TopBar.tsx#L8) (`<span className={styles.nick}>{nick}</span>`). Styled at TopBar.module.css:15-18. Nick plumbed via [AppShell.tsx:36](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/components/AppShell.tsx#L36).
- **Logout today** — lives in the sidebar, **not** the TopBar: [Sidebar.tsx:42-46](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/components/Sidebar.tsx#L42-L46) (`⏻` glyph when collapsed). Handler chain: `Sidebar` ← `AppShell.tsx:41` ← `App.tsx:47`; handler impl at [App.tsx:52-61](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/App.tsx#L52-L61) (calls `postLogout()`, then `window.location.reload()`).
- **Planned change** — convert the nickname `<span>` into a dropdown trigger + menu containing a "Logout" item. `TopBar` currently receives only `{ nick }`; it will need an `onLogout` prop added ([TopBar.tsx:4](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/components/TopBar.tsx#L4)). Decide whether to remove the duplicate logout in [Sidebar.tsx:42-46](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/src/frontend/src/components/Sidebar.tsx#L42-L46) (relocation, not just addition). No icon library exists; the dropdown caret and any logout glyph will be inline SVG / Unicode, matching the existing `⏻` convention.

## Code References

- `src/frontend/src/main.tsx:8-11` — SPA entry; candidate mount point for a site-wide cookie banner.
- `src/frontend/src/App.tsx:18-29` — auth state (`useState<Me|null>`); `getMe()` fetch.
- `src/frontend/src/App.tsx:39-41` — **the single auth seam** (`if (!me.authenticated) return <Welcome …/>`).
- `src/frontend/src/App.tsx:52-61` — `handleLogout` implementation.
- `src/frontend/src/api.ts:13-24` — `Me` / `MeUser` / `Role` types.
- `src/frontend/src/api.ts:29` — `document.cookie` CSRF read (must remain unblocked).
- `src/frontend/src/components/Welcome.tsx:9-28` — unauth landing page; header at 12-15 is the Star-button anchor; hero CTA at 23 is the secondary option.
- `src/frontend/src/components/Welcome.module.css:24-36` — `.loginBtn` (button style to mirror); `.cta` at 68-82.
- `src/frontend/src/components/TopBar.tsx:1-11` — nickname render (Action 3 anchor).
- `src/frontend/src/components/Sidebar.tsx:42-46` — current logout location (Action 3).
- `src/frontend/src/components/AppShell.tsx:36,41` — `TopBar` / `Sidebar` prop plumbing.
- `src/frontend/src/styles.css:16-36` — CSS custom-property palette to match/theme against.
- `src/frontend/package.json:17-22` — runtime deps (React 18.3, react-router-dom 6.30, recharts 2.15; **no Oval/Redux**).
- `src/target_o_meter/settings.py:223-243` — session + CSRF cookie config; `SameSite=Lax` at 231 is load-bearing for OIDC.
- `.railway/railway.ts:141` — canonical repo URL `krkruk/target-o-meter`.

## Architecture Insights

- **Single-source-of-truth auth seam.** `App.tsx:39` is the only branch that decides unauthed vs authed. Both Action 1 and Action 2 belong inside `Welcome`, which is already unauth-only — no extra conditionals needed.
- **No design system / no shared components.** Each component owns its `*.module.css`; there is no `<Button>`, no `<Icon>`, no `<Dropdown>`. The Star button and the nick dropdown will each be hand-rolled with their own CSS Module, mirroring `Welcome.module.css` / `TopBar.module.css`.
- **Zero-icon-dependency stance.** The codebase deliberately avoids icon libraries (uses Unicode glyphs and one `<img>` SVG). Do not introduce one for a single star glyph — use inline SVG.
- **CSS-custom-property theming.** Both the Star button and the cookie popup should reference existing `--color-*` tokens; vanilla-cookieconsent's `--cc-*` vars override cleanly in the same `:root` block.
- **`AGENTS.md §1` drift.** The doc says Oval + Redux; the code has neither. Flag for a separate doc fix — out of scope for this change.

## Historical Context (from prior changes)

- [context/archive/2026-07-25-sign-in-empty-dashboard/research.md](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/context/archive/2026-07-25-sign-in-empty-dashboard/research.md) — flagged the `SameSite=Lax` OIDC chain as fragile; relevant caveat for any cookie-handling work (do not insert logic between the OIDC redirect and the session cookie).
- [context/archive/2026-07-28-infrastructure-as-code/research.md](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/context/archive/2026-07-28-infrastructure-as-code/research.md) — confirms the repo URL `krkruk/target-o-meter`.
- [context/foundation/lessons.md](https://github.com/krkruk/target-o-meter/blob/c0b2f289a1d670f22f8b3b84e6352bf4618ea937/context/foundation/lessons.md) — re-read; no frontend/UI priors (lessons are all backend/deploy). No applicable constraints for this change.

## Related Research

- None — this is the first `research.md` under `context/changes/`. No prior research artifacts exist in the active `changes/` tree.

## Open Questions

1. **GitHub URL delivery to the SPA.** Hardcode the literal `https://github.com/krkruk/target-o-meter`, or introduce the project's first `VITE_GITHUB_URL` env var (no precedent)? Plan should pick one.
2. **Cookie popup coverage.** User said "start on the unauthenticated page" — mount inside `Welcome` only, or site-wide in `main.tsx`? (Site-wide is the safer default if the user ever wants it on authed pages too.)
3. **Star button placement.** Header next to Login (recommended, matches the existing top-bar layout) vs hero copy next to "Get started" — confirm in plan.
4. **Sidebar logout after Action 3.** Remove the duplicate Sidebar entry, or keep both? Confirm in plan.
5. **AGENTS.md §1 drift.** Oval + Redux are claimed but absent. Out of scope here; flag for a separate doc fix.
6. **`AGENTS.md §1` also claims `django-storages` S3 etc.** — irrelevant to this UI change, but note the doc-vs-code drift exists in more than one place.
