# GitHub Star button + EU cookie consent popup — Plan Brief

> Full plan: `context/changes/add-github-button-and-cookies/plan.md`
> Research: `context/changes/add-github-button-and-cookies/research.md`

## What & Why

Add a GitHub "Star" button to the unauthenticated landing page, a GDPR/ePrivacy
cookie consent banner across the SPA, and a `/privacy` page for the banner to
link to. The motivation is twofold: surface the repo for social proof on first
contact, and put a forward-looking consent layer in place *before* any tracking
lands — so when analytics is added later, the gating store and policy link are
already wired.

## Starting Point

The SPA is plain React 18 + react-router-dom + recharts (no Oval/Redux, despite
`AGENTS.md §1`). A single auth seam at `App.tsx:39` renders `Welcome` for
unauthenticated visitors. There are no external links, no icon library, no UI
library, no `VITE_*` env vars, and only strictly-necessary Django session + CSRF
cookies today. The BFF serves the SPA shell via a catch-all `re_path` that
swallows any unknown top-level path.

## Desired End State

An unauthenticated visitor sees a Star button in the `Welcome` header (opens the
repo in a new tab) and, on first visit, a cookie banner with equally-prominent
Accept/Reject whose "Cookie Policy" link goes to a real `/privacy` page listing
the two strictly-necessary cookies. The banner stores consent (timestamped,
revision-versioned) and never re-prompts until the revision bumps. Login/CSRF
flows are untouched.

## Key Decisions Made

| Decision                              | Choice                                                            | Why (1 sentence)                                                                                          | Source   |
| ------------------------------------- | ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | -------- |
| Scope of this change                  | GitHub button + cookie popup + `/privacy` page only               | The nick→logout dropdown (research "Action 3") is an authed-area change unrelated to cookies/GitHub.       | Plan     |
| GitHub URL delivery                   | Hardcode the literal `https://github.com/krkruk/target-o-meter`   | No precedent for `VITE_*` env vars; the repo URL never effectively changes, and hardcoding speeds dev.     | Plan     |
| Cookie popup coverage                 | Site-wide — initialize in `main.tsx` around `<App />`             | The library owns its DOM portal and reads consent from storage; one mount is cleanest and future-proofs.  | Plan     |
| Star button placement                 | `Welcome` header, immediately left of Login                       | Mirrors the existing brand-left/actions-right top-bar layout; header is the conventional home for it.      | Plan     |
| Cookie categories exposed at launch   | `necessary` only                                                  | Nothing tracks today; honest UI that only shows categories which actually exist. Expand when needed.      | Plan     |
| Banner policy link                    | Create a stub `/privacy` page                                     | Gives the banner a real, server-rendered target instead of a dead link or README hand-wave.                | Plan     |
| Policy page rendering                 | Standalone Django template (does NOT extend `base.html`)          | Extending `base.html` boots the SPA, and the auth seam would render `Welcome` for unauthed visitors.      | Research |
| Cookie library                        | `vanilla-cookieconsent` v3                                        | Strongest EU/GDPR story, granular categories, built-in TS types, 0 deps, themeable via `--cc-*` vars.     | Research |

## Scope

**In scope:**
- GitHub Star button in `Welcome` header (inline SVG, hardcoded URL, external-link convention).
- Standalone `/privacy` page (Django template + view + URL route + catch-all exclusion).
- `vanilla-cookieconsent` v3 banner, site-wide mount, necessary-category only, themed.
- Tests: Welcome Star-link assertions, `/privacy` system test, consent wrapper config test.

**Out of scope:**
- Nick → logout dropdown (separate change).
- Analytics/marketing cookie categories (add when something tracks; bump `revision`).
- `VITE_GITHUB_URL` env var.
- Fixing `AGENTS.md §1` Oval/Redux doc drift.
- Full legal privacy-policy authoring (stub states the accurate cookie inventory).

## Architecture / Approach

```
Phase 1 (frontend, independent)
  Welcome.tsx ── adds <a> Star (hardcoded URL, target=_blank, rel=noopener)
  Welcome.module.css ── adds .starBtn + .headerActions
  Welcome.test.tsx ── pins href/target/rel

Phase 2 (Django, must precede Phase 3)
  templates/privacy.html ── standalone (no SPA boot)
  src/bff/views.py ── privacy() view
  src/bff/urls.py ── path("privacy",…) + catch-all excludes "privacy"
  tests/system/test_privacy_page.py ── 200 + not-the-shell assertions

Phase 3 (frontend, depends on Phase 2)
  package.json ── +vanilla-cookieconsent@^3.1.0
  src/frontend/src/cookieConsent.ts ── run()/destroy() wrapper, necessary-only, /privacy link
  src/frontend/src/main.tsx ── useEffect mounts banner once site-wide
  src/frontend/src/styles.css ── --cc-* theme overrides on :root
  src/frontend/src/cookieConsent.test.ts ── config smoke test
```

## Phases at a Glance

| Phase | What it delivers                                | Key risk                                                                                  |
| ----- | ----------------------------------------------- | ----------------------------------------------------------------------------------------- |
| 1. GitHub Star button   | External anchor in `Welcome` header   | Narrow-viewport overflow of Star + Login in the top bar.                                  |
| 2. `/privacy` page      | Standalone server-rendered policy page | Catch-all `re_path` shadows the route if the negative-lookahead exclusion is missed.      |
| 3. Cookie consent popup | Site-wide GDPR banner mounted in `main.tsx` | Banner interferes with OIDC/CSRF if it mistakenly gates strictly-necessary cookies.       |

**Prerequisites:** none beyond the existing SPA + BFF stack. No DB changes, no
new services.
**Estimated effort:** ~1–2 sessions; three small phases, mostly hand-rolled UI
plus one tiny Django view.

## Open Risks & Assumptions

- **Catch-all exclusion is easy to miss.** If `privacy` isn't added to the
  `re_path` negative lookahead, `/privacy` serves the SPA shell and the banner's
  link is dead. Phase 2's test explicitly asserts the page is NOT the shell.
- **Consent banner vs. OIDC chain.** The banner must not insert logic between
  the OIDC redirect and the `SameSite=Lax` session cookie. Prior research flagged
  this chain as fragile; Phase 3's manual checklist re-verifies login end-to-end.
- **Stub policy is not legal review.** The `/privacy` page states the technically-
  accurate cookie inventory; it is not a substitute for jurisdiction-specific
  legal review if the audience or tracking scope expands.
- **No analytics gating today.** The banner's value is its consent store + the
  `/privacy` page; it gates nothing yet by design. When analytics lands, add the
  category and bump `revision`.

## Success Criteria (Summary)

- Star button opens the repo in a new tab from the `Welcome` header.
- `/privacy` renders a standalone page listing `sessionid` + `csrftoken` (no SPA boot).
- First-visit banner appears with equally-prominent Accept/Reject, stores
  consent, re-opens via "Cookie settings", and links to `/privacy`.
- Login + CSRF flows are unaffected (`make check` + `make fe-test` + `make be-test` green).
