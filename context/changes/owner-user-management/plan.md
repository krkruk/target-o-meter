# Owner User Management (S-04) Implementation Plan

## Overview

Ship the owner-only admin page for managing access to Target-o-meter: a searchable, paginated list of registered users with the power to **ban** (duration + reason) and **delete** them. Banning is a real, enforceable action — a banned user is blocked at the OAuth callback on their next login and shown a server-rendered page explaining why and until when. This is slice S-04 of the roadmap (FR-003, FR-004; **FR-005 invite-only is dropped** — the owner will use Auth0 directly to invite people).

Roadmap slice: **S-04** (`context/foundation/roadmap.md:104-116`). PRD refs: FR-003 (list users), FR-004 (remove user). Prerequisite F-01 (`oauth-roles-scaffold`) is done. The slice also introduces the SPA's first client-side router (React Router), which S-02/S-03 will reuse.

## Current State Analysis

The backend is **already owner-ready** (F-01 + S-01 archived done). The frontend has an Admin **seam** but no page.

### Backend — what exists and works

- **`identity.User`** (`src/domains/identity/models.py:90-156`): UUID PK, unique `sub`, CI-unique `nick` (`CharField(max_length=64)`), `has_set_nick` (S-01), `is_staff`. Derived `role`/`is_owner` from `OWNER_SUB_ID` env. **No ban/status/`is_active` field exists** — `AbstractBaseUser` does not contribute `is_active`, and nothing else adds one. S-04 adds the `Ban` model (a history table), not a column on `User`.
- **`list_users()`** (`src/domains/identity/services.py:103-112`): already returns real `list[UserOut]` (`nick`, `role`, `has_set_nick`) — **no `sub`, no pagination, no search, no ban status.** The F-01 "empty list" comment was about missing data, not a missing read path. S-04 rewrites this to expose `sub`, paginate, filter, and join ban state.
- **`require_owner(request)`** (`src/bff/api.py:45-64`): a **body-call helper, not an auth-list callable** (documented rationale at `api.py:20-29`). Every new owner route MUST follow `auth=session_auth` + `require_owner(request)` as the first body line — the existing `GET /v1/users` (`src/bff/routers/owner_routes.py:28-37`) is the template.
- **OAuth callback** (`src/bff/routers/auth_routes.py:81-144`): the single point that resolves a user from Auth0's `sub` and calls `django.contrib.auth.login()`. Flow: `oauth.auth0.authorize_access_token(request)` → `userinfo["sub"]` → `get_or_create_user_row(sub)` → (first-login logging) → `user.backend = ...; login(request, user)` → redirect. **This is where the ban check goes** — right after resolving the user, before `login()`.
- **Removed-user handling already 401s** (`api.py:52-61`, `session_routes.py:42,59`): a deleted user's next request cleanly returns 401, never 500. The delete action reuses a proven path.
- **Domain-exception precedent**: `NickTakenError` (`services.py:19-27`) — a typed exception the BFF maps to `HttpError(409)`. S-04 defines `UserNotFoundError` (and reuses `ErrorOut` at `dtos.py:70-73`) for the new failure modes.
- **Settings**: `SESSION_COOKIE_AGE = 60 * 60 * 8` (8h) at `src/target_o_meter/settings.py:227`. S-04 reduces this to 2h to bound the window a banned-but-already-logged-in user can keep operating (the owner chose login-only enforcement).
- **Cookie hardening** (`settings.py:217-245`): `SESSION_COOKIE_SAMESITE = "Lax"` is **load-bearing** for the OIDC callback — do not tighten. `CSRF_COOKIE_HTTPONLY = False` so the SPA can read `csrftoken`. Cookie-age change is independent of both.

### Frontend — what exists

- **`src/frontend/`** is a Vite + React + TS SPA, runtime deps only `react` + `react-dom` (`package.json:16-18`). **No client-side router** (`App.tsx:6` explicitly defers it to S-02+). **No Redux/Oval** — plain `useState`.
- **The Admin seam already exists**: `Sidebar.tsx:34-38` renders a **disabled** Admin `<button>` when `isOwner`; `AppShell.tsx:19` computes `isOwner = me.user?.role === 'owner'`. The test pinning `toBeDisabled()` lives at `AppShell.test.tsx:86-102` — it must be updated when the button is wired.
- **API client** (`src/frontend/src/api.ts`): hand-written `fetch` wrappers. Types: `Role = 'owner' | 'user'`, `MeUser { nick, role, has_set_nick }`, `Me { authenticated, user }`. CSRF via `readCsrfToken()` + `jsonHeaders()`. **No generic `apiGet`/`apiPost` helper, no list/pagination helper.** `getMe()` special-cases 401; everything else throws on non-ok.
- **Components**: `Welcome`, `AppShell` (+ `Sidebar`, `TopBar`), `NickPrompt` (the only modal — `.overlay`/`.card` in `NickPrompt.module.css`; pending/error/submit-disabled pattern at `NickPrompt.tsx:20-43`).
- **CSS Modules + global custom properties** (`src/frontend/src/styles.css:16-24`): `--color-bg`, `--color-fg`, `--color-primary`, `--color-muted`, `--color-border`, `--sidebar-width`, `--sidebar-collapsed-width`. `data-*` attributes drive state styling (e.g. `Sidebar[data-collapsed]`).
- **Test patterns**: Vitest + jsdom + `@testing-library/react` + `@testing-library/user-event`. API mocked via `vi.spyOn(api, ...)` or `vi.spyOn(globalThis, 'fetch')`. Assertions use role/accessible-name queries; `makeMe(overrides)` factory at `AppShell.test.tsx:16-22`.

### Key Discoveries

- **The `sub` must surface to the owner.** Today `UserOut` deliberately omits `sub` (`dtos.py:7-9`) — that invariant holds for the `/v1/me` owner-facing response, but **the owner admin list is a different audience**: the owner needs `sub` to identify users across nick changes and to match rows against the Auth0 dashboard. S-04 introduces a separate `AdminUserOut` DTO carrying `sub`, not a relaxation of `UserOut`.
- **Ban enforcement is a single gate, not a middleware.** The owner chose login-only enforcement (no per-request middleware). The OAuth callback (`auth_routes.py:81-144`) is the one place a session is created — checking the active ban there, before `login()`, is sufficient. The 8h→2h cookie-age reduction bounds the worst case (a user already logged in when banned) to ≤2h.
- **`list_users()` extension is load-bearing for the whole slice.** Today it returns `User.objects.all()` mapped to DTOs with no `sub`, no pagination, no filtering, no join. The admin list needs all four. The cleanest path is a new `list_users_for_owner(...)` service that returns `AdminUserOut` with embedded ban-state fields, leaving the existing `list_users()` untouched (it backs no UI today, but its signature is part of the domain surface).
- **React Router is introduced here, ahead of S-02/S-03.** S-01 explicitly deferred it; the admin page is the first true second screen. Wiring it now means S-02/S-03 land as routes, not as view-state hacks. `App.tsx`'s single-`useState` branch becomes `<BrowserRouter>` + `<Routes>`.
- **The banned page is intentionally NOT part of the SPA.** A banned user must not load the app shell at all (no `/v1/me`, no client bundle beyond the banned page). It is a server-rendered Django template, served by the callback when it detects an active ban — the same pattern as F-01's old `welcome.html`/`main.html`, just one new template.
- **`onHome` is already an optional `Sidebar` prop that `AppShell` doesn't pass** (`Sidebar.tsx:8`, `AppShell.tsx:25-30`) — a precedent for adding `onAdmin` the same way. But with React Router, the Admin button becomes a `<Link to="/admin">`, not an `onAdmin` callback.

## Desired End State

After this plan, the owner can:

1. **Click "Admin" in the sidebar** (the previously-disabled button is now a link) and land on `/admin` — a paginated (20/page) list of all registered users, each row showing nick + `sub` + a ban status chip ("Active ban · 23h left" in red, "Banned before" in grey, or nothing) + a Ban button + a Delete button. A search box filters by nick or `sub` (case-insensitive substring).
2. **Ban a user** — click Ban → modal opens with a duration dropdown (1h / 1 day / 7 days / 30 days) and a required free-text justification → submit persists a `Ban` row (with computed `banned_until`) and the list updates to show the active ban.
3. **Unban a user early** — on a row with an active ban, the button label adapts (e.g. "Unban") and clicking it lifts the ban (`lifted_at` set); the row's chip becomes "Banned before".
4. **Delete a user** — click Delete → confirmation modal with a reminder note ("Also delete this person in Auth0 → Users tab; Target-o-meter cannot do that for you") → confirm hard-deletes the `User` row (and their `Ban` rows cascade); the row disappears.
5. **As a banned user, be blocked at login** — on the next OAuth login, the callback detects the active ban, **does not create a session**, and renders a server-rendered `/banned`-style page showing the reason and the expiry ("until <datetime>"). The banned user cannot reach the SPA.
6. **Trust the bounded session** — `SESSION_COOKIE_AGE` is reduced from 8h to 2h, so a user banned while already logged in loses access within at most 2h (next request after expiry → 401 → re-login → blocked).
7. **Non-owners see nothing** — `/admin` is owner-only: an anonymous user hitting `/admin` gets the SPA welcome (no session); an authenticated non-owner hitting `/admin/users` API gets 403 (`require_owner`).
8. **Run `make check` + `make be-test` + `make fe-test`** all green, with new backend tests (Ban model, services, owner routes, ban-at-login enforcement, banned page) and new Vitest tests (router, admin list, ban modal, unban, delete modal).

## What We're NOT Doing

- **FR-005 invite-only toggle** — dropped (owner decision): the owner will use Auth0 directly to invite people. S-04 ships FR-003 + FR-004 + the ban feature only.
- **Soft-delete for users** — parked per roadmap (`context/foundation/roadmap.md:171`). Delete is hard-delete with an Auth0 reminder note.
- **Per-request ban middleware** — the owner chose login-only enforcement. No middleware; the 8h→2h cookie reduction bounds the worst case.
- **Email notifications on ban/delete** — out of scope (Zero Email Storage; the system has no email).
- **Ban appeal flow, ban reason categorization/presets, ban history UI** — the Ban *model* keeps full history (one row per event), but the UI shows only active + last-expired state per user. A dedicated ban-history view is post-MVP.
- **Bulk actions (bulk ban/delete)** — one user at a time.
- **Dashboard content, photo capture, scoring** — those are S-02/S-03. The admin page is the only new screen here.
- **Editing the owner's own row** — the owner appears in the list (they're a registered user), but banning/deleting oneself is guarded against (can't ban or delete the owner; see Phase 2).
- **A second OAuth provider, gallery upload, manual hole correction** — all parked per roadmap.

## Implementation Approach

Four phases, each independently verifiable. Phases 1–2 are backend-vertical (pytest, no frontend); Phases 3–4 are frontend-vertical (Vitest + manual gate).

- **Phase 1** lands the `Ban` model + migration, the identity services (`ban_user`, `unban_user`, `get_active_ban`, `list_users_for_owner` with pagination/search/ban-state), DTOs, and the `SESSION_COOKIE_AGE` reduction. Pure Python + ORM; no HTTP. Fully testable via pytest.
- **Phase 2** lands the BFF owner routes (paginated list, ban, unban, delete) behind `require_owner`, the ban check inside the OAuth callback (skip `login()` → render the banned template), and the banned page. System-tested end-to-end with the dev-bypass + mocked Auth0 token exchange.
- **Phase 3** introduces `react-router-dom`, refactors `App.tsx` to `<BrowserRouter>` + `<Routes>`, wires the Sidebar Admin button to `<Link to="/admin">`, and builds the read-only admin users page (search, pagination, status chips). No mutations yet.
- **Phase 4** adds the Ban modal (duration dropdown + required free-text reason), the Unban action, and the Delete confirmation modal (with Auth0 reminder note), plus the manual end-to-end gate.

**House style**: backend mirrors the DDD + BFF conventions F-01/S-01 established — router in `src/bff/routers/owner_routes.py` → service in `src/domains/identity/services.py` → ORM in `src/domains/identity/models.py`; Pydantic DTOs in `dtos.py`; one class per file per `context/foundation/lessons.md:12-17` (`Ban` lives in its own `ban.py`, not crammed into `models.py`); import-linter contracts 1 & 2 must stay green. Frontend follows the React + TS + CSS Modules conventions from S-01 (`data-*` for state styling, `var(--color-*)`, role-based RTL queries in tests).

**Architectural enforcement** (must stay green at every phase boundary):
- `.importlinter` contract:1 (domain independence) — new identity services must not import from `vision` or `core`.
- `.importlinter` contract:2 (BFF above domains) — new routes live in `src/bff/routers/owner_routes.py` and call services; the router never touches the ORM.
- `ruff check .` clean; `make check` + `make be-test` + `make fe-test` green.

## Critical Implementation Details

- **The ban check goes between user-resolution and `login()` in the callback.** `auth_routes.py:107-141` already calls `get_or_create_user_row(sub)` → `(user, is_first_login_ever)` and then `login(request, user)`. Insert `ban = get_active_ban(user.sub)` (or by `user_uuid`) after the resolve and before `user.backend = ...`. If `ban` is non-None: do **not** call `login()`; do **not** create a session; render the banned template with `{"reason": ban.reason, "banned_until": ban.banned_until}` and return. The existing first-login `logger.warning` (lines 127-133) stays above the ban check — a brand-new user has no ban, so the ordering is safe.
- **`get_or_create_user_row` returns an ORM object; `get_active_ban` must accept that.** The service takes primitives (per the DTO-only boundary), but the callback already holds the `User` instance. Pass `user.sub` (or `user.id`) into `get_active_ban` — do not introduce a new ORM-returning service just for the callback. The existing `get_or_create_user_row` is the documented singular exception (`services.py:47-71`).
- **CSRF on the new owner mutations.** django-ninja auto-enforces CSRF for `SessionAuth` on non-GET (`bff/api.py:1-8` docstring; proven by S-01's `PATCH /v1/me` tests). The SPA reads `csrftoken` via `readCsrfToken()` and sends `X-CSRFToken`. POST/DELETE owner routes inherit this — no new middleware. The system tests must assert 403 (or 401) without the header, mirroring `tests/system/test_auth_flow.py`'s CSRF assertions.
- **The owner cannot be banned or deleted.** Both `ban_user` and `delete_user` services must refuse if the target's `sub == OWNER_SUB_ID` (i.e. `target.is_owner`). Raise a typed domain exception (`CannotModifyOwnerError`) → BFF maps to `HttpError(409, "Cannot ban or remove the owner")`. The list can still *show* the owner (with no ban/delete buttons rendered client-side, and a server-side guard for direct API hits).
- **Pagination contract: offset + page-size, not cursor.** Hobbyist scale (`prd.md:9` `users: small`) — standard offset/limit is fine and simpler for the SPA. `?q=<query>&page=<n>&page_size=20`; default page 1, page_size 20, max page_size 50 (clamp). Response includes `items`, `page`, `page_size`, `total`, `total_pages` so the SPA can render pager controls without a second round-trip.
- **`banned_until` is computed at ban time, not at check time.** `Ban` stores `banned_until = banned_at + duration` as a concrete datetime (UTC, `USE_TZ=True` — verify in `settings.py`). "Active ban" = `banned_until > now() AND lifted_at IS NULL`. This makes the check a single indexed comparison and makes expiry automatic (no cron).
- **`SESSION_COOKIE_AGE` change is migration-free and immediate.** It's a setting, not a schema change. Existing sessions are unaffected until they expire; new sessions get the 2h bound. No data migration, no rollback concern.
- **The banned page template must NOT include the SPA bundle.** It is a standalone Django template (like F-01's old `welcome.html`) — no `{% vite %}` tags, no `<div id="root">`, no JS. A banned user must not load the client app. It extends a minimal base (or is fully standalone) and shows reason + expiry + "try again after <time>" + a back-to-login link.
- **React Router's basename and the SPA catch-all.** The SPA is mounted at `/` by the `index` view (`bff/views.py`). React Router uses default basename `/`. `/admin` is a client-side route — Django serves the same SPA shell for `/admin` (the index view must accept any path that isn't a real backend route, OR a specific `path("admin", index)` + the SPA handles the rest client-side). The cleanest: add `path("admin", index)` to `urls.py` so the SPA shell loads at `/admin` and React Router renders the admin route. `/v1/*`, `/login`, `/callback`, `/logout`, `/banned`, and `/admin/` (Django admin) must keep winning over the SPA — URLconf order already handles this (`urls.py:33-43`).

## Phase 1: Backend — `Ban` model + identity services

### Overview

Land the data model and pure-Python services the BFF will call: the `Ban` history model + migration 0003, the ban/unban/active-ban services, the paginated+searchable `list_users_for_owner`, the new DTOs (`AdminUserOut`, `BanStatusOut`, paginated list response, request bodies), and the `SESSION_COOKIE_AGE` reduction. No HTTP in this phase — fully pytest-verifiable.

### Changes Required:

#### 1.1 `Ban` model

**File**: `src/domains/identity/ban.py` (new — one class per file per `lessons.md:12-17`)

**Intent**: The ban history table. One row per ban event; active = `banned_until > now() AND lifted_at IS NULL`.

**Contract**: `class Ban(models.Model)`. Fields: `id` (UUID PK, `default=uuid.uuid4`, `editable=False`), `user` (`ForeignKey` to `identity.User`, `on_delete=CASCADE` — bans go when the user is hard-deleted; this is intra-domain, so FK is allowed, AGENTS.md §5 forbids only *cross-domain* FKs), `reason` (`TextField`, required — the owner's free-text justification), `duration_kind` (`CharField(max_length=8)` constrained to a `class Duration(TextChoices): ONE_HOUR="1h"; ONE_DAY="1d"; SEVEN_DAYS="7d"; THIRTY_DAYS="30d"`), `banned_at` (`DateTimeField(auto_now_add=True)`), `banned_until` (`DateTimeField` — concrete UTC datetime computed at creation from `banned_at + duration`), `lifted_at` (`DateTimeField(null=True, blank=True)` — set when the owner unbans early; null while active/expired-by-time). `class Meta: app_label="identity"; db_table="identity_ban"; ordering=["-banned_at"]` (most recent first). `__str__` returns `f"Ban(user={self.user_id}, until={self.banned_until}, lifted={self.lifted_at})"`. Add a module-level `_DURATION_DELTAS = {Duration.ONE_HOUR: timedelta(hours=1), ...}` mapping used by the service to compute `banned_until`. **Do not** add a `class Meta: constraints` uniqueness constraint — multiple historical bans per user are allowed; "active" is the query, not a constraint.

#### 1.2 Migration 0003

**File**: `src/domains/identity/migrations/0003_ban.py` (new — generated, not hand-written)

**Intent**: Create the `identity_ban` table.

**Contract**: Run `uv run python src/manage.py makemigrations identity` after 1.1; verify it produces `0003_ban.py` creating the `identity_ban` table with the FK to `identity_user` (`on_delete=CASCADE`) and all fields above. Schema-only — no `RunPython`. Then `uv run python src/manage.py migrate` applies cleanly.

#### 1.3 Identity DTOs

**File**: `src/domains/identity/dtos.py` (extend)

**Intent**: The typed boundary for owner-admin traffic. `AdminUserOut` is the projection that *does* carry `sub` (the owner needs it); `UserOut` (`dtos.py:38-44`) is unchanged and stays `sub`-less for `/v1/me`.

**Contract**: Add Pydantic `BaseModel` classes:
- `BanStatusOut`: `is_banned: bool`, `reason: str | None`, `banned_until: datetime | None`, `lifted_at: datetime | None`, `has_prior_ban: bool` (true if any past ban exists, active or expired — drives the "Banned before" chip).
- `AdminUserOut`: `user_uuid: UUID`, `sub: str`, `nick: str`, `has_set_nick: bool`, `ban: BanStatusOut`. (No `role` — every listed user is a `user`; the owner is identified separately. If the owner appears in the list, mark them via a separate `is_owner: bool` field so the SPA can hide ban/delete buttons.)
- `AdminUserListOut`: `items: list[AdminUserOut]`, `page: int`, `page_size: int`, `total: int`, `total_pages: int`.
- `BanIn`: `duration: Literal["1h","1d","7d","30d"]`, `reason: str = Field(min_length=5, max_length=500)`. (Required free-text justification, min 5 chars per the owner's decision.)
- `UserContextDTO` (`dtos.py:19-35`): **unchanged** — ban state is not part of the session context; `/v1/me` stays as-is.

#### 1.4 Ban services

**File**: `src/domains/identity/services.py` (extend) — or, if the file is getting large, a new `src/domains/identity/ban_services.py` (one logical concern per file; mirror how `set_nick` lives in `services.py`).

**Intent**: Pure business logic for create-ban, lift-ban, get-active-ban. Primitives in, DTOs out; typed exceptions; no ORM/IntegrityError crossing the seam.

**Contract**:
- `ban_user(*, user_sub: str, duration: str, reason: str) -> BanStatusOut` — look up `User` by `sub` (raise `UserNotFoundError` if missing → BFF 404); **refuse if `target.is_owner`** (raise `CannotModifyOwnerError` → BFF 409); validate `duration` is one of the four literals (raise `ValueError` → BFF 422) and `reason` length (the DTO enforces, but double-check); compute `banned_until = now() + _DURATION_DELTAS[duration]`; create the `Ban` row; return `BanStatusOut` reflecting the new active ban.
- `unban_user(*, user_sub: str) -> BanStatusOut` — look up user (404 if missing); find the active ban (`banned_until > now() AND lifted_at IS NULL`); if none, raise `NoActiveBanError` → BFF 409; set `lifted_at = now()`, save with `update_fields=["lifted_at"]`; return updated `BanStatusOut` (`is_banned=False`, `has_prior_ban=True`, `lifted_at` populated).
- `get_active_ban(user_sub: str) -> Ban | None` — the read accessor the OAuth callback uses. Returns the active `Ban` row (or `None`). This is the ONE ORM-returning read for the callback's convenience, mirroring the `get_or_create_user_row` exception pattern (`services.py:47-71`); document it as such. (Alternatively return a DTO and have the callback check `dto.is_banned` — prefer DTO to keep the boundary clean; the callback then never imports `Ban`.)
- `get_ban_status(user_sub: str) -> BanStatusOut` — builds the per-user ban status used by the list (queries active + any-prior in one go).
- Typed exceptions: `UserNotFoundError`, `CannotModifyOwnerError`, `NoActiveBanError` — mirror `NickTakenError` (`services.py:19-27`).

#### 1.5 Owner-list service

**File**: `src/domains/identity/services.py` (extend) — `list_users_for_owner`

**Intent**: Replace the no-`sub`/no-pagination `list_users` for the admin context. The old `list_users()` stays (it backs no UI, but is part of the domain surface); the owner list is a new, richer function.

**Contract**: `def list_users_for_owner(*, q: str = "", page: int = 1, page_size: int = 20) -> AdminUserListOut:` — clamp `page_size` to ≤50, `page` ≥1; build `qs = User.objects.all()`; if `q` non-empty, filter `Q(nick__icontains=q) | Q(sub__icontains=q)`; order by `nick` asc (case-insensitive — `Lower("nick")`); paginate via `[offset:offset+page_size]` (offset pagination, hobbyist scale); for the page's users, bulk-fetch active bans + prior-ban existence in two queries (avoid N+1 — `Ban.objects.filter(user__in=page_users, banned_until__gt=now())` for active, `Ban.objects.filter(user__in=page_users).values("user_id").annotate(...)` for prior); build `AdminUserOut` per row with `BanStatusOut`; compute `total` and `total_pages`. Return `AdminUserListOut`. The owner row is included with `is_owner=True`.

#### 1.6 `SESSION_COOKIE_AGE` reduction

**File**: `src/target_o_meter/settings.py`

**Intent**: Bound the window a banned-but-already-logged-in user can keep operating (the owner chose login-only enforcement). 8h → 2h.

**Contract**: Change `SESSION_COOKIE_AGE = 60 * 60 * 8` (`settings.py:227`) to `SESSION_COOKIE_AGE = 60 * 60 * 2`. Update the adjacent comment to cite the rationale (ban-enforcement bound). No other cookie settings change (`SAMESITE=Lax`, `HTTPONLY=True`, `SECURE` env-gated all stay).

#### 1.7 Identity unit tests

**Files**: `src/domains/identity/tests/test_ban_services.py` (new), extend `src/domains/identity/tests/conftest.py` + `test_utils.py`

**Intent**: Cover the ban lifecycle, the owner-guard, the active/prior query, and the paginated list. Mirror `test_services.py` patterns.

**Contract**: `pytestmark = pytest.mark.django_db`. Tests: (a) `ban_user` creates a `Ban` with correct `banned_until` for each duration; (b) `ban_user` on the owner raises `CannotModifyOwnerError`; (c) `ban_user` on unknown sub raises `UserNotFoundError`; (d) `ban_user` with short reason raises (DTO validation, tested via the route in Phase 2, but the service double-checks); (e) `get_active_ban`/`get_ban_status` returns active before `banned_until`, expired after (use `freezegun` or `timezone.now()` manipulation — check what's already available; if none, compute a past `banned_until` directly); (f) `unban_user` sets `lifted_at`, after which `get_ban_status` shows `is_banned=False, has_prior_ban=True`; (g) `unban_user` with no active ban raises `NoActiveBanError`; (h) `list_users_for_owner` paginates (seed 25 users, page 1 → 20, page 2 → 5), filters by nick and sub (case-insensitive), orders by nick asc, and attaches correct `BanStatusOut` (one user actively banned, one previously banned). Extend `test_utils.py` with `make_ban(*, user, duration, reason, banned_until=None, lifted_at=None) -> Ban` and `make_user` stays as-is (ban is created via the service or `make_ban`, not by extending `make_user`). Conftest gains a `banned_user` fixture.

### Success Criteria:

#### Automated Verification:

- Migration applies cleanly on a fresh DB: `rm -f db.sqlite3 && uv run python src/manage.py migrate`
- `uv run python src/manage.py makemigrations identity` produces no unexpected diff (0003 already exists)
- Identity unit tests pass: `uv run pytest src/domains/identity/tests/`
- System check passes: `uv run python src/manage.py check`
- Lint passes: `uv run ruff check .`
- Import-linter passes (contract:1 domain independence, contract:2 BFF above domains): `uv run lint-imports`

#### Manual Verification:

- `uv run python src/manage.py shell -c "from src.domains.identity.ban import Ban; print(Ban._meta.db_table)"` prints `identity_ban`
- `showmigrations identity` shows `0001_initial`, `0002_has_set_nick`, `0003_ban` all applied

**Implementation Note**: After completing this phase and all automated verification passes, pause for manual confirmation from the human that the migration + model landed cleanly before proceeding to Phase 2.

---

## Phase 2: Backend — BFF owner routes + ban enforcement + banned page

### Overview

Land the HTTP layer: the real owner routes (paginated list, ban, unban, delete) behind `require_owner`, the ban check injected into the OAuth callback (skip `login()` → render the banned template), and the server-rendered banned page. System-tested end-to-end with the dev-bypass + a mocked Auth0 token exchange (no real Auth0).

### Changes Required:

#### 2.1 Owner routes — list / ban / unban / delete

**File**: `src/bff/routers/owner_routes.py` (extend)

**Intent**: The real owner actions, all behind `require_owner`. Mirrors the existing `GET /v1/users` shape (`owner_routes.py:28-37`).

**Contract**: Keep the existing `router = Router()` and `GET /users` (or fold it into the new list — see below). Add:
- `GET /users` (rewrite of the demo route): `auth=session_auth`, `response={200: AdminUserListOut}`. First body line `require_owner(request)`. Query params: `q: str = ""`, `page: int = 1`, `page_size: int = 20`. Calls `list_users_for_owner(q=q, page=page, page_size=page_size)`. (The old `list[UserOut]` return becomes `AdminUserListOut`; update the demo route's response schema. The old `list_users()` service is no longer called here but stays in the domain.)
- `POST /users/{user_sub}/ban`: `auth=session_auth`, `response={200: BanStatusOut, 404: ErrorOut, 409: ErrorOut, 422: ErrorOut}`, body `payload: BanIn`. `require_owner(request)` first. Calls `ban_user(user_sub=user_sub, duration=payload.duration, reason=payload.reason)`; map `UserNotFoundError → HttpError(404)`, `CannotModifyOwnerError → HttpError(409, "Cannot ban the owner")`, `ValueError → HttpError(422)`. Returns the `BanStatusOut`.
- `POST /users/{user_sub}/unban`: `auth=session_auth`, `response={200: BanStatusOut, 404: ErrorOut, 409: ErrorOut}`. `require_owner(request)` first. Calls `unban_user(user_sub=user_sub)`; map `UserNotFoundError → 404`, `NoActiveBanError → HttpError(409, "No active ban")`. Returns updated `BanStatusOut`.
- `DELETE /users/{user_sub}`: `auth=session_auth`, `response={204: None, 404: ErrorOut, 409: ErrorOut}`. `require_owner(request)` first. Look up the user (404 if missing); **refuse if `target.is_owner`** (409, same guard as ban — reuse `CannotModifyOwnerError` from the service, or duplicate the check in the route if the service isn't called for delete); `target.delete()` (cascades to `Ban` rows); return 204.

#### 2.2 Ban enforcement in the OAuth callback

**File**: `src/bff/routers/auth_routes.py` (extend `callback` at `:81-144`)

**Intent**: The single enforcement point. After resolving the user, before `login()`, check the active ban; if present, render the banned page and return without creating a session.

**Contract**: In `callback(request)`, after `user, is_first_login_ever = get_or_create_user_row(sub)` (`auth_routes.py:107-141`) and after the first-login `logger.warning` block, insert: `ban_status = get_ban_status(sub)` (DTO form, clean boundary); `if ban_status.is_banned:` render `banned.html` with `{"reason": ban_status.reason, "banned_until": ban_status.banned_until}` and **return** that `HttpResponse` — do **not** set `user.backend`, do **not** call `login()`, do **not** redirect to `/`. The existing happy path (`user.backend = ...; login(request, user); redirect("oauth_next" or "/")`) follows unchanged in the `else`. Add a `logger.info("Login blocked — active ban until %s", ban_status.banned_until)` for observability. The brand-new-user case (`is_first_login_ever`) can't have a ban, so the ordering (first-login log, then ban check) is safe.

#### 2.3 Banned page template + view

**Files**: `templates/banned.html` (new), `src/bff/views.py` (extend) or render inline in `auth_routes.py`

**Intent**: The server-rendered page a banned user sees. NOT part of the SPA — no `{% vite %}`, no `<div id="root">`, no JS. Shows reason + expiry + "try again later" + a link back to `/login`.

**Contract**: `banned.html` extends a minimal base (either `templates/base.html` stripped down, or fully standalone `<html>` — prefer standalone so it cannot accidentally inherit SPA script tags). Content: a heading ("You are banned"), the reason verbatim (`{{ reason }}`), the expiry as a human-readable datetime (`{{ banned_until|date:"..." }}` — confirm `USE_TZ` and the user's timezone expectation; render in UTC with a timezone label for MVP), and a link to `/login` ("Try again later"). The callback renders it via `render(request, "banned.html", {"reason": ..., "banned_until": ...})`. **No new URL route** — the template is rendered directly by the callback, not served at a path. (If a separate `/banned` route is desired for testing, it can be added, but the callback's direct render is the primary path.)

#### 2.4 System tests

**Files**: `tests/system/test_owner_routes.py` (new), `tests/system/test_ban_enforcement.py` (new), extend `tests/system/conftest.py`

**Intent**: Cover the owner routes' 401/403/200/404/409 contract and the ban-at-login enforcement. Use the existing `force_login` + dev-bypass patterns; mock the Auth0 token exchange for the callback test.

**Contract**: `pytestmark = [pytest.mark.django_db, pytest.mark.dev]`. `test_owner_routes.py`:
- `GET /v1/users` → 401 anonymous, 403 plain user, 200 owner (returns `AdminUserListOut` with `items/page/total`).
- Pagination: seed 25 users, `?page=2&page_size=20` returns 5 items, `total=25`, `total_pages=2`.
- Search: `?q=alice` filters nick; `?q=auth0|123` filters sub.
- Ban status attached: a banned user's row has `ban.is_banned=True`; a previously-banned user has `ban.has_prior_ban=True, is_banned=False`.
- `POST /v1/users/{sub}/ban` → 200 happy path (creates ban, returns status); 404 unknown sub; 409 on owner; 422 on short reason / invalid duration; **403 without `X-CSRFToken`** (locks the auto-CSRF invariant for POST, mirroring S-01's PATCH test); 200 with valid CSRF.
- `POST /v1/users/{sub}/unban` → 200 after a ban; 409 with no active ban; 404 unknown.
- `DELETE /v1/users/{sub}` → 204 happy path (row + cascaded bans gone); 404 unknown; 409 on owner; **403 without CSRF**.

`test_ban_enforcement.py`:
- Mock `oauth.auth0.authorize_access_token` (via `monkeypatch` on `src.bff.oauth.oauth.auth0` or the Authlib client) to return a token whose `userinfo` has a banned user's `sub`. Hit `/callback` → assert the response renders `banned.html` (status 200, body contains the reason), **and** assert no session was created (`client.session` empty / `client.user` anonymous).
- Same mock with an unbanned user's `sub` → `/callback` creates the session and redirects to `/`.
- A user whose ban has expired (`banned_until < now()`) → `/callback` logs in normally (not blocked).

Extend `tests/system/conftest.py` with owner/user clients and a `banned_user` fixture (creates a user + an active `Ban` via `make_ban` or the `ban_user` service).

### Success Criteria:

#### Automated Verification:

- System tests pass: `uv run pytest tests/system/test_owner_routes.py tests/system/test_ban_enforcement.py`
- Full backend suite green: `uv run pytest`
- `uv run python src/manage.py check` passes
- Import-linter passes (contract:1 + contract:2): `uv run lint-imports`
- `uv run ruff check .` passes

#### Manual Verification:

- `curl -i http://localhost:8000/v1/users` (no session) → 401; with dev-bypass-as-owner → 200 with paginated shape; with dev-bypass-as-user → 403.
- (Ban-at-login against real Auth0 is a Phase 4 manual gate — here it's verified via the mocked-callback system test.)

**Implementation Note**: After completing this phase and all automated verification passes, pause for manual confirmation from the human that the owner-route RBAC contract and the mocked ban-enforcement hold before proceeding to Phase 3.

---

## Phase 3: Frontend — React Router + admin users page (read-only)

### Overview

Introduce `react-router-dom`, refactor `App.tsx` from the single-`useState` branch to `<BrowserRouter>` + `<Routes>`, wire the Sidebar Admin button to a `<Link to="/admin">`, and build the read-only admin users page (search, pagination, status chips). No mutations yet — Phase 4 adds the modals.

### Changes Required:

#### 3.1 Add `react-router-dom`

**File**: `src/frontend/package.json`

**Intent**: The SPA's first client-side router. S-01 deferred it; the admin page is the first true second screen, and S-02/S-03 will reuse it.

**Contract**: Add `"react-router-dom": "^6.x"` to `dependencies` (latest 6.x stable; do NOT jump to 7.x without checking React 18 compat). Run `npm install`. Verify `npx tsc --noEmit` is clean.

#### 3.2 Refactor `App.tsx` to React Router

**File**: `src/frontend/src/App.tsx`

**Intent**: Replace the single-`useState` branch with a routed shell. The welcome/app-shell decision still comes from `GET /v1/me`; routing adds the `/admin` child inside the app shell.

**Contract**: Wrap the root in `<BrowserRouter>` + `<Routes>`. The auth seam (`getMe()` on mount) stays — it still decides welcome vs. shell. Structure: a top-level route that, once `me.authenticated`, renders `<AppShell me={me} onLogout={...} />` with an `<Outlet>` inside its `<main>`; nested routes: `index` → dashboard placeholder (existing), `"admin"` → `<AdminUsersPage />` (Phase 3.4). The `NickPrompt` still overlays when `!me.user.has_set_nick`. Unauthenticated → `<Welcome>` (no nested routes). Keep the loading state. The `me === null` and `!me.authenticated` branches are unchanged in behavior.

#### 3.3 `AppShell` renders `<Outlet>`; Sidebar Admin button becomes a `<Link>`

**Files**: `src/frontend/src/components/AppShell.tsx`, `Sidebar.tsx`, `Sidebar.module.css`

**Intent**: The app shell becomes a layout with a nested route outlet; the disabled Admin button becomes a real link.

**Contract**: `AppShell`'s `<main>` renders `<Outlet />` (replaces the `.placeholder` div — the dashboard placeholder moves to the `index` route's element, or stays as the default when no child route matches). `Sidebar`: replace the disabled Admin `<button>` (`Sidebar.tsx:34-38`) with `<Link to="/admin">` rendered as a button-styled anchor (keep the `⚙`/`Admin` label, drop `disabled`, drop `aria-disabled`). Keep the `isOwner` gate (only owners see the link). Update the test at `AppShell.test.tsx:86-102` that asserts `toBeDisabled()` — it now asserts the Admin link is present and not disabled for owners, absent for plain users.

#### 3.4 `AdminUsersPage` — read-only list

**Files**: `src/frontend/src/components/AdminUsersPage.tsx`, `AdminUsersPage.module.css`, `src/frontend/src/api.ts` (extend)

**Intent**: The owner admin page. Searchable, paginated list of users with ban-status chips. Read-only in Phase 3.

**Contract**: `api.ts` additions: `type AdminUser = { user_uuid: string; sub: string; nick: string; has_set_nick: boolean; is_owner: boolean; ban: { is_banned: boolean; reason: string | null; banned_until: string | null; lifted_at: string | null; has_prior_ban: boolean } }`; `type AdminUserList = { items: AdminUser[]; page: number; page_size: number; total: number; total_pages: number }`; `async function getAdminUsers(params: { q?: string; page?: number; page_size?: number }): Promise<AdminUserList>` (GET `/v1/users?q=...&page=...&page_size=...`, throws on non-ok including 403). `AdminUsersPage`: owns `query`/`page`/`data`/`pending`/`error` state; fetches on mount and whenever `query`/`page` changes (debounce `query` ~250ms). Renders: a search input (controlled, aria-label "Search users"), the list as rows (nick, sub in monospace, ban-status chip — red "Active ban · <Xh left>" or grey "Banned before" or nothing), a pager (Prev / Page N of M / Next) when `total_pages > 1`. Empty state ("No users match."). Error state (esp. 403 → "Owner privileges required"). Ban + Delete buttons are rendered as placeholders/disabled in Phase 3 (enabled in Phase 4). Use `var(--color-*)` from `styles.css`; add new tokens (`--color-danger`, `--color-warning`) if needed for chips.

#### 3.5 Vitest tests for the list page

**File**: `src/frontend/src/components/AdminUsersPage.test.tsx` (new), update `AppShell.test.tsx`, `App.test.tsx`

**Intent**: Cover the search/pagination render, the role-based access (403 message), and the router wiring.

**Contract**: `AdminUsersPage.test.tsx`: mock `getAdminUsers` via `vi.spyOn`; assert search input filters (debounced), pager renders when `total_pages > 1`, status chip shows "Active ban" for a banned user and "Banned before" for a prior-ban user, 403 → error message. Update `AppShell.test.tsx`: Admin link (not disabled button) for owners, absent for users. Update `App.test.tsx`: wrap in `<MemoryRouter>` (or the test harness handles it — check existing `App.test.tsx` setup; router-aware components need a router in tests).

### Success Criteria:

#### Automated Verification:

- `cd src/frontend && npm run test` — all Vitest tests green (new `AdminUsersPage` tests + updated `AppShell`/`App` tests).
- `cd src/frontend && npx tsc --noEmit` clean.
- `cd src/frontend && npm run build` produces `dist/`.
- `uv run pytest` green (no backend changes this phase).
- `make check` green.

#### Manual Verification:

- With `DEV_AUTH_BYPASS_SUB == OWNER_SUB_ID`: `/` lands in the app shell; the sidebar Admin link is clickable; clicking it navigates to `/admin` (URL changes); the users page renders with search + list + pager. Refreshing `/admin` keeps you on the admin page (deep link works).
- With `DEV_AUTH_BYPASS_SUB != OWNER_SUB_ID` (plain user): the Admin link is absent; manually visiting `/admin` shows the page but `getAdminUsers` 403s → "Owner privileges required" message.
- Search and pagination behave (type in the box → debounced filter; click Next → page 2).

**Implementation Note**: After completing this phase and all automated verification passes, pause for manual confirmation from the human that the router + read-only list work before proceeding to Phase 4.

---

## Phase 4: Frontend — Ban / unban / delete modals + manual gate

### Overview

Add the mutation UX: the Ban modal (duration dropdown + required free-text reason), the Unban action (adaptive button label), and the Delete confirmation modal (with the Auth0 reminder note). Plus the manual end-to-end gate: real Auth0 login as a banned user → see the banned page.

### Changes Required:

#### 4.1 API client — ban / unban / delete

**File**: `src/frontend/src/api.ts` (extend)

**Intent**: Typed wrappers for the three mutations, with CSRF on the non-GET verbs.

**Contract**: `async function banUser(user_sub: string, body: { duration: '1h'|'1d'|'7d'|'30d'; reason: string }): Promise<BanStatus>` (POST `/v1/users/${user_sub}/ban`, JSON body, `jsonHeaders()` for CSRF). `async function unbanUser(user_sub: string): Promise<BanStatus>` (POST `/v1/users/${user_sub}/unban`, CSRF). `async function deleteUser(user_sub: string): Promise<void>` (DELETE `/v1/users/${user_sub}`, CSRF; 204 → resolve). `type BanStatus = { is_banned: boolean; reason: string | null; banned_until: string | null; lifted_at: string | null; has_prior_ban: boolean }`. Each throws on non-ok (the UI maps status codes to messages: 409 → "Cannot ban the owner" / "No active ban", 404 → "User not found").

#### 4.2 Ban modal component

**Files**: `src/frontend/src/components/BanModal.tsx`, `BanModal.module.css`

**Intent**: The ban UX — duration dropdown + required free-text reason. Reuses the `NickPrompt` overlay/card pattern.

**Contract**: Props: `{ user: AdminUser; onClose: () => void; onBanned: (status: BanStatus) => void }`. State: `duration` (default `'1d'`), `reason` (string), `pending`, `error`. Renders the overlay (`role="dialog"`, `aria-label="Ban {nick}"`) with a `<select>` for duration (1h / 1 day / 7 days / 30 days), a `<textarea>` for reason (min 5 chars, max 500, required), a Cancel and a Confirm button. Submit disabled while `pending` or `reason.trim().length < 5`. On submit → `banUser(user.sub, {duration, reason})` → `onBanned(status)` (parent updates the row + closes modal). On 409 (owner) → inline error. **If the user already has an active ban** (the modal can be opened from a "View/Extend ban" affordance), pre-fill the duration and show the current reason + expiry, with the action becoming "Extend ban" (creates a new `Ban` row — the service allows multiple; the active one is what matters). Esc/overlay-click closes (unlike `NickPrompt`, this modal is dismissable).

#### 4.3 Delete modal component

**Files**: `src/frontend/src/components/DeleteUserModal.tsx`, `DeleteUserModal.module.css`

**Intent**: Confirmation with the Auth0 reminder note. Hard-delete is irreversible.

**Contract**: Props: `{ user: AdminUser; onClose: () => void; onDeleted: () => void }`. State: `pending`, `error`. Renders the overlay (`role="dialog"`, `aria-label="Delete {nick}"`) showing: "Delete {nick}?" + the Auth0 reminder note ("Also delete this person in Auth0 → Users tab; Target-o-meter cannot do that for you.") + a Cancel and a "Delete permanently" button (destructive styling via `--color-danger`). Submit → `deleteUser(user.sub)` → `onDeleted()`. On 409 (owner) → inline error ("Cannot delete the owner"). Dismissable.

#### 4.4 Wire mutations into `AdminUsersPage`

**File**: `src/frontend/src/components/AdminUsersPage.tsx` (extend)

**Intent**: Enable the Ban + Delete buttons; add adaptive labels; add Unban.

**Contract**: Per-row state: which modal is open (`null | 'ban' | 'delete'`). Buttons: if `ban.is_banned` → "Unban" button (calls `unbanUser`, optimistic or refetch) + "View ban" (opens `BanModal` in extend mode); else → "Ban" button (opens `BanModal`). "Delete" button always present (opens `DeleteUserModal`). Hide both Ban and Delete buttons when `user.is_owner` (the server guards too, but the UI should not offer the action). On successful ban/unban/delete → update the row in-place (or refetch the current page). Add Vitest cases: opening each modal, submit calls the right API, 409 shows the right message, owner row has no Ban/Delete buttons.

#### 4.5 Manual end-to-end gate (real Auth0)

**File**: none (manual)

**Intent**: Verify the full ban-enforcement flow against real Auth0 — the one path the mocked system test can't prove.

**Contract**: With the owner logged in, ban a test user (any duration). Log out. Log in as the banned test user (real Auth0) → land on the banned page (NOT the SPA shell) showing the reason and expiry. As the owner, unban the test user → log in as them again → lands in the SPA normally. As the owner, delete the test user → row disappears; confirm in the Auth0 dashboard that the user still exists there (Target-o-meter cannot delete them) and note the reminder. Verify the 2h session bound: a user banned while already logged in can still use the app until their session expires (≤2h), then is blocked on re-login.

### Success Criteria:

#### Automated Verification:

- `cd src/frontend && npm run test` — all Vitest tests green (new `BanModal`, `DeleteUserModal` tests + updated `AdminUsersPage`).
- `cd src/frontend && npx tsc --noEmit` clean.
- `cd src/frontend && npm run build` produces `dist/`.
- `uv run pytest` green.
- `make check` + `make be-test` + `make fe-test` green.

#### Manual Verification:

- Ban modal opens, duration dropdown works, reason field enforces min length, submit creates the ban and the row's chip updates to "Active ban".
- Unban lifts the ban; chip becomes "Banned before".
- Delete modal shows the Auth0 reminder; confirm removes the row.
- Owner row has no Ban/Delete buttons.
- **Real Auth0 round-trip** (Phase 4.5): banned user lands on the banned page (not the SPA); unbanned user logs in normally; deleted user is gone from the list (and still present in Auth0, as expected).

**Implementation Note**: This is the final gate. If the real-Auth0 ban block fails, the issue is almost certainly in the callback ban-check ordering (`auth_routes.py`) or the `get_ban_status` query — both verified by Phase 2's mocked system test, so a failure here points to a real-Auth0-specific issue (e.g. the `sub` format mismatch) rather than the logic.

---

## Testing Strategy

### Unit Tests:

- **Ban services** (`src/domains/identity/tests/test_ban_services.py`, Phase 1.7) — `ban_user` per-duration `banned_until`, owner-guard (`CannotModifyOwnerError`), unknown-user (`UserNotFoundError`), short-reason validation; `get_ban_status`/`get_active_ban` active-vs-expired; `unban_user` sets `lifted_at`, `NoActiveBanError` when none active; `list_users_for_owner` pagination (25→20+5), nick+sub search (CI), nick-asc ordering, ban-status attachment.

### Integration / System Tests:

- **Owner routes** (`tests/system/test_owner_routes.py`, Phase 2.4) — `GET /v1/users` 401/403/200 + pagination + search + ban-status; `POST /ban` 200/404/409(owner)/422(short reason)/403(no CSRF); `POST /unban` 200/409/404; `DELETE` 204/404/409(owner)/403(no CSRF).
- **Ban enforcement** (`tests/system/test_ban_enforcement.py`, Phase 2.4) — mocked-callback: banned user → `banned.html` rendered + no session; unbanned → session created + redirect; expired ban → logs in normally.

### Frontend Tests (Vitest):

- `AdminUsersPage` (Phase 3.5) — search/pagination render, status chips, 403 handling.
- `BanModal` (Phase 4.2) — duration select, reason min-length, submit calls `banUser`, 409 inline error, dismissable.
- `DeleteUserModal` (Phase 4.3) — Auth0 reminder shown, submit calls `deleteUser`, 409 inline error.
- Updated `AppShell.test.tsx` (Admin link, not disabled button) and `App.test.tsx` (router-aware).

### Deferred (manual gate):

- Real-Auth0 ban round-trip (Phase 4.5) — the one path the mocked test can't prove; manual, once, with the owner's Auth0 tenant.

### Manual Testing Steps:

1. **Phase 1**: shell check `Ban._meta.db_table` → `identity_ban`; `showmigrations identity` shows 0003 applied.
2. **Phase 2**: `curl /v1/users` 401/403/200; mocked ban-enforcement system test green.
3. **Phase 3**: bypass-as-owner → `/admin` renders list + search + pager; bypass-as-user → no Admin link; deep-link `/admin` works.
4. **Phase 4**: ban/unban/delete via the modals; **real Auth0** banned-user round-trip.

## Performance Considerations

- `list_users_for_owner` avoids N+1 via two bulk queries (active bans + prior-ban existence) for the page's users — O(page_size) not O(total). At hobbyist scale (`users: small`), even the naive path would be fine, but the bulk fetch is cheap insurance.
- Offset pagination (`[offset:offset+limit]`) is fine at hobbyist scale; degrades past ~5k rows. Not a concern for MVP; cursor pagination is post-MVP if growth forces it.
- The ban check in the callback adds one query per login (the `get_ban_status` lookup). Logins are infrequent; negligible.
- `SESSION_COOKIE_AGE` 8h→2h means users re-login slightly more often. Acceptable per the owner's ban-enforcement decision; the dev-bypass is unaffected (stateless).
- React Router adds ~10kb gzipped to the bundle. Negligible.

## Migration Notes

- **`0003_ban`** is schema-only, creates `identity_ban` with a FK to `identity_user` (`on_delete=CASCADE`). No data migration, no backfill. Rollback: `migrate identity 0002` drops the table; `User` data is untouched. No production data exists yet (app not deployed).
- **`SESSION_COOKIE_AGE` change is migration-free.** It's a setting, not a schema change. Existing sessions are unaffected until they expire; new sessions get 2h. No rollback concern (revert the line).
- **`GET /v1/users` response shape changes** (`list[UserOut]` → `AdminUserListOut`). The only consumer today is the F-01 demo route (no frontend). Phase 3's `getAdminUsers` consumes the new shape. No external API consumers exist.
- **`react-router-dom` is a new runtime dep** — adds to `package.json` `dependencies`. `npm install` required; `npm run build` reproduces. No rollback concern (revert + rebuild).
- **No production data exists** (the app isn't deployed), so all migrations run against empty/dev DBs only. No coordinated downtime needed.

## References

- Roadmap S-04 row: `context/foundation/roadmap.md:104-116`
- PRD FR-003/004: `context/foundation/prd.md:65-71`
- F-01 plan (house-style template, OAuth callback detail): `context/archive/2026-07-24-oauth-roles-scaffold/plan.md`
- S-01 plan (SPA patterns, `/v1/me` seam, Sidebar Admin seam): `context/archive/2026-07-25-sign-in-empty-dashboard/plan.md`
- Lessons (one class per file): `context/foundation/lessons.md:12-17`
- AGENTS.md §5 (boundary rules — no cross-domain ORM, no HTTP in domains, DTOs only), §6 (BFF atomicity)
- django-ninja SessionAuth/CSRF: https://django-ninja.dev/guides/authentication/
- React Router 6: https://reactrouter.com/en/6.x

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Backend — `Ban` model + identity services

#### Automated

- [x] 1.1 `Ban` model in `src/domains/identity/ban.py` (UUID PK, FK→User CASCADE, reason, duration_kind, banned_at, banned_until, lifted_at; `Duration` TextChoices; `_DURATION_DELTAS`) — 80d61da
- [x] 1.2 Migration `0003_ban` generated + applies cleanly (`makemigrations identity` → `migrate`) — 80d61da
- [x] 1.3 Identity DTOs (`BanStatusOut`, `AdminUserOut`, `AdminUserListOut`, `BanIn`) in `dtos.py`; `UserOut`/`UserContextDTO` unchanged — 80d61da
- [x] 1.4 Ban services (`ban_user`, `unban_user`, `get_active_ban`/`get_ban_status`) + typed exceptions (`UserNotFoundError`, `CannotModifyOwnerError`, `NoActiveBanError`) — 80d61da
- [x] 1.5 `list_users_for_owner(q, page, page_size)` with nick+sub CI search, nick-asc order, bulk ban-status attach, offset pagination — 80d61da
- [x] 1.6 `SESSION_COOKIE_AGE` 8h → 2h in `settings.py` with rationale comment — 80d61da
- [x] 1.7 Identity unit tests (`test_ban_services.py` + `test_utils.make_ban` + `banned_user` fixture) — 80d61da
- [x] 1.8 `uv run pytest src/domains/identity/tests/` green — 80d61da
- [x] 1.9 `uv run ruff check .` + `uv run lint-imports` + `uv run python src/manage.py check` green — 80d61da

#### Manual

- [x] 1.10 `shell -c "Ban._meta.db_table"` prints `identity_ban`; `showmigrations identity` shows 0003 applied — 80d61da

### Phase 2: Backend — BFF owner routes + ban enforcement + banned page

#### Automated

- [x] 2.1 Owner routes in `owner_routes.py`: `GET /v1/users` (paginated `AdminUserListOut`), `POST /v1/users/{sub}/ban`, `POST /v1/users/{sub}/unban`, `DELETE /v1/users/{sub}` — all `require_owner` first, CSRF auto-enforced — 5d135d9
- [x] 2.2 Ban check in OAuth callback (`auth_routes.py`): after `get_or_create_user_row`, before `login()`; if `ban_status.is_banned` → render `banned.html`, no session — 5d135d9
- [x] 2.3 `templates/banned.html` (standalone, no SPA bundle) + render from callback — 5d135d9
- [x] 2.4 System tests: `test_owner_routes.py` (401/403/200/404/409/422 + CSRF 403 + pagination + search + ban-status) + `test_ban_enforcement.py` (mocked callback: banned→banned page+no session, unbanned→session, expired→login) — 5d135d9
- [x] 2.5 `uv run pytest` green; `uv run ruff check .` + `uv run lint-imports` + `uv run python src/manage.py check` green — 5d135d9

#### Manual

- [x] 2.6 `curl /v1/users` 401 anon / 403 user / 200 owner (paginated shape) — verified via system tests (test_owner_routes.py RBAC + paginated-shape cases) — 5d135d9

### Phase 3: Frontend — React Router + admin users page (read-only)

#### Automated

- [x] 3.1 `react-router-dom` ^6.x added to `package.json` `dependencies`; `npm install`; `tsc --noEmit` clean — pre-existing (S-02 landed the router); verified clean — 5f6df73
- [x] 3.2 `App.tsx` refactored to `<BrowserRouter>` + `<Routes>`; auth seam preserved; nested routes (`index` → dashboard placeholder, `admin` → `AdminUsersPage`) — pre-existing (S-02); the `/admin` route added in AppShell — 5f6df73
- [x] 3.3 `AppShell` renders `<Outlet>`; `Sidebar` Admin `<button>` → `<Link to="/admin">` (drop `disabled`); `AppShell.test.tsx` updated — 5f6df73
- [x] 3.4 `AdminUsersPage` (search, pagination, status chips, 403 handling) + `getAdminUsers`/`AdminUser` types in `api.ts` — 5f6df73
- [x] 3.5 `AdminUsersPage.test.tsx` (search, pager, chips, 403); updated `AppShell.test.tsx` (Admin link, not disabled button) — 5f6df73
- [x] 3.6 `npm run test` + `tsc --noEmit` + `npm run build` clean; `make check` green — 5f6df73

#### Manual

- [x] 3.7 Bypass-as-owner → `/admin` list + search + pager + deep-link; bypass-as-user → no Admin link, `/admin` shows 403 message — deep-link covered by test_spa_deep_links.py (+/admin); the full flow covered by the Playwright acceptance test (Phase 4) — 5f6df73

### Phase 4: Frontend — Ban / unban / delete modals + manual gate

#### Automated

- [x] 4.1 `api.ts`: `banUser`, `unbanUser`, `deleteUser` (CSRF on all) + `BanStatus` type
- [x] 4.2 `BanModal` (duration select + required free-text reason, min 5 chars; extend mode for active bans; dismissable) + test
- [x] 4.3 `DeleteUserModal` (Auth0 reminder note + destructive confirm) + test
- [x] 4.4 `AdminUsersPage` wiring: adaptive Ban/Unban/View-ban buttons, Delete button, hide actions on owner row; tests
- [x] 4.5 `npm run test` + `tsc --noEmit` + `npm run build` clean; `make check` + `make be-test` + `make fe-test` green

#### Manual

- [x] 4.6 Ban modal → row chip "Active ban"; Unban → "Banned before"; Delete modal → row gone; owner row has no action buttons — covered by the Playwright acceptance test (tests-acceptance/owner-management.spec.ts) driving the live SPA through the full ban→chip→unban→chip→delete→gone flow + the owner-row-has-no-buttons assertion
- [ ] 4.7 Real Auth0 round-trip: banned user → `/banned` page (not SPA); unbanned → logs in; deleted → gone from list (still in Auth0 as expected); 2h session bound holds — DEFERRED: requires live Auth0 creds; the enforcement logic is covered by tests/system/test_ban_enforcement.py (mocked callback: active ban → banned page + no session, unbanned → session, expired → login)
