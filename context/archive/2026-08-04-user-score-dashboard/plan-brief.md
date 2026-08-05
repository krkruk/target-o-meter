# User Score Dashboard — Plan Brief

> Full plan: `context/changes/user-score-dashboard/plan.md`
> Research: `context/changes/user-score-dashboard/research.md`

## What & Why

A standalone `/scores` dashboard where a user reviews all their scores, previews the target image with per-shot scores, modifies an existing score, and hard-deletes one — plus a "Recent results" section (max 20) on the home page that reuses the same row component, and a "Score dashboard" entry in the left menu under "Home". Today the user can only see a capped, unpaginated recent list (10 rows) and cannot edit or delete an accepted score (`AcceptedResult` is documented immutable; there is no list/detail/update/delete route).

## Starting Point

The "score record" is `AcceptedResult` (`src/domains/vision/models.py:75-121`); the per-shot scores are JSON on `holes`, the bolded total is the denormalized `score_average`. Images live on the sibling `ScoringJob` (reached via plain-UUID `source_job`, no FK), already served through an existing BFF proxy route reused for preview. The accept/edit form `Results.tsx` is already fully editable but is a route page, not a modal. There is no Redux/RTK/React Query — state is `useState` + `fetch` in `api.ts`, plain CSS Modules, hardcoded labels. Pagination + hard-delete + ownership-check conventions already exist (in the identity domain) to mirror.

## Desired End State

From the menu's "Score dashboard" (which highlights active) the user reaches `/scores`: their scores paginated (default 20, dropdown 10/20/30/50), grouped under day headers, most-recent first; each row shows date + bolded score on the left and Preview / Modify / Delete on the right. Preview shows the target image + a one-line immutable per-shot scores list. Modify opens a modal reusing the editable accept-form controls (Modify = PATCH that recomputes the average; Cancel just closes). Delete opens a confirm modal that removes the row and best-effort deletes the S3 objects (the `ScoringJob` audit row is retained). Home shows the same row component for the 20 most recent scores. Users see and touch only their own scores (404 on others').

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| API namespace | All 4 new routes under `/v1/scores` | One REST resource; matches lessons.md "URIs name resources not actions". | Plan |
| Modify form-factor | Fresh `<ModifyModal>` mirroring `BanModal` | Zero risk to the working accept flow; isolation over DRY. | Plan |
| Delete scope | `AcceptedResult` + storage objects only | Matches the documented "ScoringJob survives as audit record" posture. | Research |
| Delete ordering | DB-first (atomic), best-effort S3 after | S3 isn't transactional; DB is source of truth; matches existing orphan posture. | Research |
| Detail read for Modify | New `GET /v1/scores/{id}` (accepted snapshot) | Reading the CV job would clobber previous corrections with raw detector output. | Plan |
| `updated_at` field | Add it | Tracks modifications auditably; matches `ScoringJob`; cleanly supports recompute. | Plan |
| Home "Recent results" | Switch to new `GET /v1/scores?page_size=20` | Single source of truth — home and dashboard share one endpoint + component. | Plan |
| Active-link styling | `NavLink` for Home / Score dashboard / Admin | Consistent menu UX; small, contained change. | Plan |
| Row sharing | New shared `<ScoreRow>` + `<ScoreList>` | True reuse; identical rows on home + dashboard (per spec). | Plan |
| Pagination defaults | default 20, options 10/20/30/50 | Verbatim from the spec. | Plan |
| Day-bucketing | Day headers, rows listed within | "Single block" reads as visual grouping, not collapse-by-default. | Plan |

## Scope

**In scope:**
- `updated_at` migration on `AcceptedResult` + docstring/admin updates (immutability lift).
- 4 new BFF routes under `/v1/scores`: list (paginated), detail, PATCH (update), DELETE.
- `ScoringStorage.delete_upload` + `delete_paths` (new storage-delete surface).
- Vision services: `list_results`, `get_result`, `update_result`, `delete_result`.
- Shared `<ScoreRow>` + `<ScoreList>` (day-bucketed); `<ScoreDashboard>` page with pagination; `<ScorePreview>`, `<ModifyModal>`, `<DeleteModal>`.
- `/scores` route + "Score dashboard" menu entry (plain Link in Phase 3, NavLink active styling in Phase 4).
- Home "Recent results" switched to the shared component (page_size=20).

**Out of scope:**
- Soft-delete; deleting the `ScoringJob` row; presigned S3 URLs; async/q2 deletion; S3-orphan sweeper; re-aggregation rebuild; i18n; changes to `target.svg`; retrofitting active styling beyond the menu.

## Architecture / Approach

Backend-first in two phases (read APIs + migration → mutate APIs), so the frontend always builds against a working contract; then frontend in two phases (shared list/row + read-only dashboard → actions + NavLink). The 4 new routes are a REST resource at `/v1/scores`; ownership is enforced in the domain (`PermissionError` on missing/not-mine → BFF 404), pagination mirrors `list_users_for_owner` (page/page_size clamp ≤50, `{items,total,total_pages}`), delete is DB-first atomic + best-effort S3 (log failures, don't raise). The frontend reuses `useState`+`fetch` (no Redux), CSS Modules with palette tokens, and hand-rolled modals mirroring `BanModal`/`DeleteUserModal`. The Modify modal fetches the accepted snapshot (`GET /v1/scores/{id}`) so a previous correction isn't clobbered by raw detector output.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Backend — Read APIs + migration | `updated_at` migration; `GET /v1/scores` (list) + `GET /v1/scores/{id}` (detail) | Pagination/clamp math; 404-not-mine invariant |
| 2. Backend — UPDATE + DELETE | `PATCH` + `DELETE` routes; `ScoringStorage.delete_*`; immutability lift | S3 best-effort failure handling; `score_average` recompute |
| 3. Frontend — Shared list/row + read-only dashboard | `<ScoreRow>`/`<ScoreList>`/`<ScoreDashboard>`; `/scores` route + menu entry; home switch | Day-bucketing correctness; pagination dropdown state |
| 4. Frontend — Actions + NavLink | Preview, `<ModifyModal>`, `<DeleteModal>`, active menu styling | Modify reusing editable controls without regressing the accept route |

**Prerequisites:** Local dev runs (`make dev`); `db.sqlite3` with at least a few accepted results to exercise pagination; FS dev (`USE_S3=False`) for manual delete verification.
**Estimated effort:** ~3–4 sessions across 4 phases (backend 2 phases are the larger half; frontend 2 phases lean on the modal/row patterns already in the codebase).

## Open Risks & Assumptions

- **Immutability lift** contradicts a documented invariant (`models.py:96-98`) — the plan reverses it explicitly and updates the docstrings/admin, but reviewers should sign off on the stance change.
- **S3 deletion is best-effort and after-commit** — orphaned objects are possible on S3 failure (accepted, matches existing posture; a sweeper is explicitly out of scope).
- **`NavLink` conversion touches Home** (currently a `<button onClick={onHome}>`) — the `onHome` prop wiring in `AppShell.tsx:42` must be removed/updated in the same change.
- **Stale `mocks/dashboard.ts`** (0–100 scale, per research) — the new DTOs stay on the 0–10 scale; do not regress.
- **`<ResultsList>` becomes unused after Phase 3.6** — left in place for safety; confirm and remove in a follow-up.

## Success Criteria (Summary)

- A user can list/preview/modify/delete their scores on `/scores`, and the home "Recent results" shows the same rows (max 20); Modify recomputes the average, Delete removes the row + S3 objects (FS-verified); owner isolation holds (404 on others'); `make check` + `make be-test` + `make fe-test` green.
