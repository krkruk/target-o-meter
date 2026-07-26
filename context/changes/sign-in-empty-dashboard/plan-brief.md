# Sign-in + empty dashboard (S-01) — Plan Brief

> Full plan: `context/changes/sign-in-empty-dashboard/plan.md`
> Research: `context/changes/sign-in-empty-dashboard/research.md`

## What & Why

Ship the **first frontend code in the repo**: a React + TypeScript SPA that replaces F-01's server-rendered templates and lands the user-facing shell for roadmap slice S-01 — an unauthenticated **welcome page** and an authenticated **app shell** (top bar + collapsible left menu with Home + Logout-at-bottom, dashboard placeholder). It also closes two deferred F-01 commitments: the **username-on-first-login** UX (FR-002) and **POST + CSRF logout**.

The motivation is the S-01 roadmap outcome: a signed-in user must land on a real page. Everything downstream (S-02 capture/detect, S-03 accept/aggregate) hangs off this shell.

## Starting Point

The backend is **already SPA-ready** (F-01 archived done 2026-07-25): a working Auth0 OIDC + PKCE flow, a zero-email `identity.User` model keyed by `sub`, `GET /api/me` returning `{authenticated, user:{nick, role}}`, a dev-auth-bypass for local dev, and cookie settings pre-hardened for an SPA (`CSRF_COOKIE_HTTPONLY=False`, `SameSite=Lax`). The frontend is **entirely absent** — `src/frontend/` is empty, `django-vite` is declared but unwired, no `package.json`/React/Vite/CSS/SVG exists.

## Desired End State

A developer runs `runserver` + `npm run dev`, opens `/`, and sees either the welcome page (unauthed) or the app shell (authed, decided client-side via `GET /v1/me`). First-login users set a nick via an inline prompt (`PATCH /v1/me`); logout is a POST (CSRF-enforced). All backend changes are pytest-green; all React components are Vitest-green; the production bundle builds; the real Auth0 round-trip is confirmed once (Phase 4 manual gate).

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Frontend stack | React + TS + Vite, CSS Modules; **defer Redux/Oval** to S-02 | Matches AGENTS.md's React mandate + typed backend; Redux has nothing to store in two screens. | Plan (research Open Decision §1) |
| First-login signal | Explicit `has_set_nick BooleanField` + migration | Unambiguous, survives a user who wants a `shooter-*` nick; one trivial migration. | Plan (research §4) |
| Welcome-page SVG | Vite asset (`src/frontend/assets/target.svg`) | Gets hashing/caching; matches SPA-first posture; original artwork avoids licensing/privacy concerns. | Plan (research §9) |
| Routing | Conditional render on `/v1/me`; **defer React Router** to S-02 | Two screens, one auth check; router is dead weight until S-02/S-03 add screens. | Plan (research Open Decision §6) |
| Logout | Change `/bff/logout` GET → **POST + CSRF** | Closes the GET-logout CSRF-soft vector F-01 flagged (plan-review F5). | Plan (F-01 commitment) |
| API versioning | `/v1/...` (major-only, REST style); drop `/bff/` and `/api/` prefixes | User requirement to drop the silly `bff` URL name and version the surface from the start; module name stays `src/bff/` per AGENTS.md §4. | Plan (user direction) |
| Auth0 dashboard URLs | Updated as a Phase 4 manual gate (user has no Auth0 account yet) | The rename changes `reverse("bff:callback")` → `/v1/callback`, registered in Auth0; honest about the external dependency. | Plan (user direction) |
| FE test strategy | Vitest + Testing Library (components) + pytest (backend) | Fast targeted feedback on new React components; backend stays on the existing `force_login` pattern. | Plan (research Open Decision §3) |

## Scope

**In scope:**
- React + TS + Vite toolchain wired via django-vite (HMR + prod build).
- Welcome page (top bar + Login top-right + hero copy + ISSF-target SVG).
- App shell (top bar with nick + collapsible left menu: Home top, Logout bottom + dashboard placeholder).
- Username-on-first-login (FR-002): `has_set_nick` migration + `set_nick` service + `PATCH /v1/me` + NickPrompt component.
- POST + CSRF logout (closes F-01's recorded commitment).
- API rename to `/v1/...` (drop `/bff/` + `/api/` prefixes).
- Vitest component tests; pytest system tests updated for the rename + new endpoint; `test_templates.py` retired/replaced.
- Manual real-Auth0 smoke test (Phase 4).

**Out of scope (deferred):**
- Dashboard content (hero stats, add-photo button, capture/upload wizard, results list, month chart) → S-02 / S-03.
- Redux / Oval → S-02 (no stateful flows yet).
- React Router → S-02 (two screens don't need it).
- Owner admin UI (FR-003/004/005) → S-04 (shell may show a disabled "Admin" entry as a seam).
- CI/CD changes for the frontend (roadmap-parked).
- A user/password form (never — Auth0 hosted UI + dev bypass cover all modes).

## Architecture / Approach

Four strictly-ordered phases. **Phase 1** is backend-only (rename + `set_nick` + POST logout), fully pytest-verifiable without a frontend — the frontend then consumes the final `/v1/...` contract, not a moving target. **Phase 2** introduces the Node/Vite/React/TS toolchain + wires django-vite, ending with a trivial render at `/` to de-risk the Django↔Vite handoff. **Phase 3** builds the real screens (welcome, shell, nick prompt) with Vitest component tests and retires `test_templates.py`. **Phase 4** is the manual real-Auth0 round-trip the user deferred to the end.

DDD + BFF boundaries are hard-enforced throughout (import-linter contracts 1 & 2 must stay green): the new route lives in `src/bff/routers/` and calls the service in `src/domains/identity/services.py`; the router never touches the ORM.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Backend foundation | `/v1/...` rename, `has_set_nick` migration, `set_nick` service, `PATCH /v1/me`, POST logout | Auth0 dashboard URLs must match `/v1/callback` (caught in Phase 4, not Phase 1) |
| 2. Frontend toolchain | Node/Vite/React/TS + django-vite wired; trivial render at `/` | django-vite dev-vs-prod manifest path / `STATICFILES_DIRS` misconfiguration |
| 3. Welcome + shell + nick prompt | Two real screens, `GET /v1/me` seam, NickPrompt, POST logout wired in UI | `test_templates.py` retirement leaving a coverage gap if Vitest tests are thin |
| 4. Real Auth0 smoke test | Manual end-to-end login/callback/set-nick/logout against real Auth0 | External dependency (Auth0 dashboard config) — code is verified by Phases 1–3 |

**Prerequisites:** F-01 done (it is). Node available locally for Phases 2–3. An Auth0 account for Phase 4 (created in-phase).
**Estimated effort:** ~4 sessions across 4 phases; Phase 1 and Phase 3 are the bulk, Phase 2 is toolchain plumbing, Phase 4 is a focused manual gate.

## Open Risks & Assumptions

- **Auth0 dashboard URL match.** The rename is verified by pytest in Phase 1, but the real OIDC redirect isn't exercised until Phase 4. If Phase 4 fails, it's almost certainly the Auth0 Allowed Callback/Logout URLs — fix in the dashboard, not code.
- **django-vite version drift.** `django-vite>=3.1.0` is pinned in `pyproject.toml:39`; the Phase 2 config (manifest path, `nested_entry`, template tags) must match the installed version's API. Verify against the installed version's docs, not memory.
- **Backfill correctness.** The `0002` migration assumes `shooter-*` nicks mean "never set." If any local dev user manually chose a `shooter-*` nick, they'd be re-prompted — harmless but surprising. Acceptable for a non-deployed app.
- **Logout without Auth0 (Phase 3 manual).** POST `/v1/logout` clears the Django session; the subsequent Auth0 `/v2/logout` redirect may error without creds. That's expected — Phase 4 confirms the full round-trip.

## Success Criteria (Summary)

- `/` serves the SPA; the welcome page shows for unauthed, the app shell for authed (decided client-side via `GET /v1/me`).
- First-login users set a nick via the prompt; the top bar updates; returning users skip it.
- Logout is POST + CSRF; the full Auth0 round-trip works (Phase 4).
- All automated guards green: `uv run pytest`, `npm run test`, `npm run build`, `uv run ruff check .`, `uv run lint-imports`, `uv run python src/manage.py check`.
