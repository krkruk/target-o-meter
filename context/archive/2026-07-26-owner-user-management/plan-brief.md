# Owner User Management (S-04) — Plan Brief

> Full plan: `context/changes/owner-user-management/plan.md`

## What & Why

Ship the owner-only admin page for managing access to Target-o-meter: a searchable, paginated list of registered users with the power to **ban** (duration + free-text reason) and **delete** them. Banning is a real, enforceable action — a banned user is blocked at the OAuth callback on their next login and shown a server-rendered page explaining why and until when. Closes roadmap slice S-04 (FR-003 list users, FR-004 remove user; **FR-005 invite-only dropped** — owner uses Auth0 directly).

## Starting Point

The backend is already owner-ready (F-01 + S-01 done): `identity.User` carries `sub`/`nick`/`has_set_nick` + a derived owner role; `require_owner` enforces 401/403; the OAuth callback (`auth_routes.py:81-144`) is the single session-creation point; `list_users()` returns real rows but no `sub`/pagination/search/ban-status. The frontend has a disabled Admin button seam (`Sidebar.tsx:34-38`) and **no client-side router** — S-01 explicitly deferred React Router to S-02+.

## Desired End State

The owner clicks Admin → `/admin` → a 20/page list (nick + `sub` + ban-status chip + Ban + Delete buttons) with nick/sub search. Ban opens a modal (duration dropdown + required reason); the row's chip turns red "Active ban · 23h left". Unban lifts early ("Banned before"). Delete confirms with an Auth0 reminder note, then hard-deletes. A banned user's next OAuth login renders a standalone `/banned` page (no SPA, no session) showing reason + expiry. `SESSION_COOKIE_AGE` drops 8h→2h to bound the worst case.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| FR-005 scope | Dropped (use Auth0) | Owner will invite via Auth0 directly; keeps this slice focused on list/ban/remove. | Plan |
| Ban enforcement point | OAuth callback only (login gate) | One enforcement point, no per-request middleware; the 8h→2h cookie reduction bounds the worst case. | Plan |
| Session cookie age | 8h → 2h | Bounds how long a banned-but-already-logged-in user can keep operating under login-only enforcement. | Plan |
| Ban data model | Separate `Ban` history table | Full audit trail per ban event; active = `banned_until > now() AND lifted_at IS NULL`; unban = set `lifted_at`. | Plan |
| Unban | Owner can lift early | Owner discretion ethos (FR-004); cheap given the history table. | Plan |
| Banned user UX | Server-rendered `/banned` page | Banned user must not load the SPA at all; reason/expiry come straight from the active Ban row. | Plan |
| Delete model | Hard-delete + Auth0 reminder | Matches roadmap's parked-soft-delete stance; the removed-user 401 path is already proven. | Plan |
| Ban reason | Free-text, required (≥5 chars) | Matches "justification reason"; gives the banned user context. | Plan |
| Search/sort | nick + sub substring, 20/page, nick asc | `sub` is the only stable identifier across nick changes; hobbyist scale tolerates substring queries. | Plan |
| Ban display | Status chip + adaptive button | Owner sees active/past ban state at a glance; button label adapts (Ban / Unban / View ban). | Plan |
| Routing | Introduce React Router now | First true second screen; S-02/S-03 will reuse it; deep-linking + back button work. | Plan |

## Scope

**In scope:** `Ban` model + migration 0003; ban/unban/list/delete identity services; owner routes (`GET /v1/users` paginated, `POST /ban`, `POST /unban`, `DELETE`); ban check in OAuth callback + `/banned` template; `SESSION_COOKIE_AGE` 8h→2h; `react-router-dom`; `AdminUsersPage` (search/pagination/chips); `BanModal` + `DeleteUserModal`.

**Out of scope:** FR-005 invite-only (use Auth0); soft-delete; per-request ban middleware; email notifications; ban appeal/preset-reasons/history-UI; bulk actions; editing the owner's own row (guarded against).

## Architecture / Approach

Backend-first vertical (Phases 1–2, pytest), then frontend vertical (Phases 3–4, Vitest + manual gate). `Ban` is its own file (`ban.py`, one-class-per-file lesson); admin DTOs are a new `AdminUserOut` that *does* carry `sub` (the owner needs it; `UserOut` for `/v1/me` stays `sub`-less). The ban check inserts between `get_or_create_user_row` and `login()` in the callback. React Router makes `/admin` a real URL; the Sidebar Admin button becomes a `<Link>`.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Backend — `Ban` model + services | `Ban` history table, ban/unban/list services, DTOs, 8h→2h cookie | `list_users_for_owner` N+1 on ban-status (mitigated via bulk queries) |
| 2. Backend — BFF routes + ban enforcement | Owner routes (paginated/CSRF), ban-at-login in callback, `/banned` page | Callback ordering — ban check must sit before `login()` and skip session creation |
| 3. Frontend — Router + read-only list | `react-router-dom`, `/admin` page (search/pager/chips) | App refactor to `<Routes>` could regress the welcome/shell split |
| 4. Frontend — Modals + manual gate | Ban modal, unban, delete modal, real-Auth0 ban round-trip | Real-Auth0 `sub` format mismatch (Phase 2's mock can't catch this) |

**Prerequisites:** F-01 done (owner scaffold, OAuth callback). S-01 done (SPA shell, Admin seam, `/v1/me`).
**Estimated effort:** ~3–4 sessions across 4 phases (2 backend, 2 frontend); Phase 4.5 needs the owner's Auth0 tenant for the real round-trip.

## Open Risks & Assumptions

- **2h session bound is a UX cost** — users re-login more often; accepted as the tradeoff for login-only ban enforcement. If it bites, a per-request middleware is the upgrade path (deferred).
- **Offset pagination** degrades past ~5k users — fine at hobbyist scale; cursor pagination is post-MVP.
- **The banned page is a new server-rendered template** alongside the SPA — intentional (banned users must not load the app), but it's a second render pipeline to maintain.
- **`react-router-dom` 6.x vs 7.x** — plan pins 6.x for React 18 compat; verify at install time.
- **Real-Auth0 ban round-trip** is a manual gate (Phase 4.5) — the mocked system test (Phase 2.4) proves the logic; only a real-`sub`-format mismatch would surface here.

## Success Criteria (Summary)

- Owner can list (search + 20/page), ban (duration + reason), unban, and delete users from `/admin`.
- A banned user's next login renders the `/banned` page with reason + expiry, and creates no session.
- Non-owners get 403 on owner routes and see no Admin link.
- `make check` + `make be-test` + `make fe-test` green; real-Auth0 ban round-trip verified manually.
