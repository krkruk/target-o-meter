# User Score Dashboard — Implementation Plan

## Overview

Build a standalone `/scores` dashboard where a user can review all their scores, preview the target image with per-shot scores, modify an existing score (reusing the accept/edit form as a modal that PATCHes), and hard-delete a score (DB row + S3 image via a confirm modal). Add a "Score dashboard" entry to the left menu under "Home", and add a "Recent results" section (max 20) to the home page that reuses the same shared row component as the dashboard.

## Current State Analysis

The "score record" is `AcceptedResult` (`src/domains/vision/models.py:75-121`). Per-shot scores live as JSON on `holes`; the bolded per-row total is the denormalized `score_average`. `AcceptedResult` stores **no image references** — images live on the sibling `ScoringJob` (reached via the plain-UUID `source_job`, no FK by design). The preview image is served through the **existing** BFF proxy `GET /v1/scoring/jobs/{id}/marked-image` — reused as-is, no new image plumbing.

The accept/edit form `src/frontend/src/components/Results.tsx` is already fully editable (per-hole `<select>` dropdowns + param selects bound to local state) but is a **route page**, not a modal. There is **no Redux / RTK / React Query** — state is component-local `useState` + direct `fetch` calls in `src/frontend/src/api.ts`. Styling is plain CSS Modules co-located per component; labels are hardcoded inline (no i18n).

What exists vs. what's new:
- Pagination convention EXISTS — `list_users_for_owner` (`src/domains/identity/services.py:293-362`): page/page_size, clamp ≤50, returns `{items, page, page_size, total, total_pages}`. Mirror it.
- Ownership-check pattern EXISTS — `get_user_context` + `filter(user_uuid=...)` + `PermissionError → 404` (not 403, to defeat ID-probing). Mirror exactly.
- Hard-delete service precedent EXISTS — `delete_user` (`src/domains/identity/services.py:278-290`).
- Paginated LIST, detail read, UPDATE, DELETE routes — **all MISSING** (genuinely new).
- `ScoringStorage` has **no delete method** — new code.
- `AcceptedResult` is documented **immutable** (`models.py:96-98`, `admin.py:14-16`) — Modify breaks this invariant; we lift it explicitly and add `updated_at`.
- No shared score-row component with action buttons exists — the existing `ResultsList` is read-only.

### Key Discoveries:

- `AcceptedResult` (`src/domains/vision/models.py:75-121`) — the dashboard row; `holes` JSON holds per-shot scores; `score_average` is the denormalized bolded total; `source_job` is the plain-UUID link to the image-bearing `ScoringJob`.
- `ScoringJob` (`models.py:13-72`) — holds `input_path` (L38, original upload key `uploads/{digest}{ext}`) and `marked_image_path` (L52, annotated preview key `jobs/{job_id}/{stem}_marked.png`).
- Preview proxy `GET /v1/scoring/jobs/{id}/marked-image` (`src/bff/routers/scoring_routes.py:173-205`) — reuse as-is for the Preview button.
- `Results.tsx` (`src/frontend/src/components/Results.tsx`) — already editable; the modal copies its controls. `handleAccept` (L88-104) → `acceptResult` POST create (cannot stand in for UPDATE); `handleReject` (L106-109) is non-destructive navigate-away (the modal's Cancel uses `onClose` instead).
- `list_users_for_owner` (`src/domains/identity/services.py:293-362`) — the pagination template. `AdminUserListOut` (`src/domains/identity/dtos.py:119-130`) — the paginated response shape.
- `delete_user` (`src/domains/identity/services.py:278-290`) + `DELETE /v1/users/{sub}` (`src/bff/routers/owner_routes.py:100-118`) — the hard-delete route shape.
- `ScoringStorage` (`src/domains/vision/pipeline/storage.py:17-219`) — no delete method today; `_safe_key`/`_safe_join` (L90-120) namespace-guard all keys; both FS and S3 backends expose `.delete(name)`.
- `BanModal.tsx` / `DeleteUserModal.tsx` — the hand-rolled modal pattern (overlay + card + Esc-dismiss, props `{target, onClose, onSuccess}`). Row→modal wiring in `AdminUsersPage.tsx:170,210,218,225-244`.
- `Sidebar.tsx:31-40` — menu topItems; insert "Score dashboard" after the Home button. `AppShell.tsx:46-57` — route table.
- `Dashboard.tsx:71-73` — existing `<ResultsList>` "Recent results"; switch to the new shared component.
- Naming hazard: the home page is already `Dashboard`/`/dashboard`. The new page uses `/scores` and `ScoreDashboard`.

## Desired End State

A user can: open the left menu and click "Score dashboard" (which highlights as active) to reach `/scores`; there they see their scores paginated (default 20, dropdown 10/20/30/50), grouped under day headers, most-recent first; each row shows date + bolded score on the left and Preview / Modify / Delete buttons on the right; Preview opens an inline view of the target image with the per-shot scores in one line below; Modify opens a modal reusing the editable accept-form controls (Modify submits a PATCH that updates the holes and recomputes the average, Cancel just closes); Delete opens a confirm modal that, on confirm, removes the row and best-effort deletes the S3 objects (the `ScoringJob` audit row is retained). The home page shows the same row component for the 20 most recent scores. Owner/non-owner isolation holds: a user sees and touches only their own scores; ID-probing returns 404.

Verification: `make check` + `make be-test` + `make fe-test` all green; manual click-through of list/paginate/preview/modify/delete on both `/scores` and home "Recent results"; manual delete leaves no row in the dashboard and the S3 object is gone (under FS dev, the file under `MEDIA_ROOT` is gone).

## What We're NOT Doing

- **No soft-delete** — hard-delete only, matching the existing `delete_user` convention.
- **No deletion of the `ScoringJob` row** — it remains the audit record (`services.py:377-378`). Only `AcceptedResult` + storage objects are removed.
- **No presigned S3 URLs** — the preview reuses the existing BFF proxy route.
- **No async/q2 task for deletion** — S3 `delete_object` is sub-second; the delete runs synchronously inside `transaction.atomic()` (best-effort S3 after DB commit), mirroring `delete_user`.
- **No sweeper for orphaned S3 objects** — out of scope (matches the existing `create_scoring_job` orphan-on-rollback posture). A future change can add one.
- **No re-aggregation rebuild** — `aggregate_for_user` and `/v1/scores/aggregations` keep working unchanged (the deleted row simply no longer appears in aggregations because it's gone from the DB).
- **No active-link retrofit beyond the menu** — `NavLink` active styling is added to Home / Score dashboard / Admin only (the whole menu); other navigation affordances are untouched.
- **No i18n** — labels stay hardcoded inline, matching convention.
- **No changes to `target.svg`** — used as-is for the Preview button icon.

## Implementation Approach

Backend-first, in two phases: (1) the read APIs + the `updated_at` migration establish the data contract and pagination shape the frontend will consume; (2) the mutate APIs (UPDATE + DELETE) add the storage-delete surface and the immutability lift. Then the frontend in two phases: (3) the shared `<ScoreRow>`/`<ScoreList>` + the read-only dashboard page + the menu/route wiring + the home "Recent results" switch; (4) the three actions (Preview, Modify modal, Delete modal) and the `NavLink` active styling. Each phase is independently testable and ends with a verification gate.

Conventions to follow (from research + lessons.md):
- **API naming** (lessons.md "URIs name resources, not actions"): plural nouns, no verbs; HTTP method carries the verb. All four new routes live under `/v1/scores`.
- **One class per file** (lessons.md): any new service class lives in its own file; `dtos.py`/`ports.py` are the exception (contract collections).
- **Ownership** — every new route resolves identity via `get_user_context(str(request.user.sub))` (401 on missing user), then the domain enforces ownership raising `PermissionError` for both "missing" and "not mine"; BFF maps `PermissionError → 404` (not 403).
- **Pagination** — mirror `list_users_for_owner`: `page=1`, `page_size=20` default, clamp `page_size` to `max(1, min(page_size, 50))`, offset slice, response carries `total` + `total_pages`.
- **CSRF** — every non-GET route is `auth=session_auth`; the frontend sends `X-CSRFToken` via `jsonHeaders()` (`api.ts:33-38`).
- **Frontend** — `useState` + `fetch` (no Redux), CSS Modules with `var(--color-*)` tokens, hardcoded labels, hand-rolled modals mirroring `BanModal`.

## Critical Implementation Details

- **Immutability lift is a PRD reversal, not a docstring tweak — amend the PRD first.** `AcceptedResult`'s immutability is sourced from PRD FR-010 (`context/foundation/prd.md:97-99`: "editing saved results is v2"), echoed at `models.py:96` and `admin.py:15-16`. AGENTS.md §2 declares the PRD the source of truth for domain constraints, so reversing this constraint requires amending the PRD before the code drifts from it. **Prerequisite (see `## Prerequisites` below):** edit FR-010's Socrates resolution to promote editing saved results from "v2" to in-scope, citing this change (`user-score-dashboard`) as the audit trail. Only after that lands does Phase 2's code change (add `updated_at = DateTimeField(auto_now=True)`, update the model + admin docstrings to say "mutable after create via PATCH /v1/scores/{id}") correctly reflect the amended constraint. The `accept_job` path's write-once semantics for `score_average` are unchanged; the new `update_result` recomputes `score_average` from the patched `holes` on every PATCH.
- **Delete resolves images through `source_job`.** The delete service takes `result_id`, owner-checks the `AcceptedResult`, reads `source_job` → `ScoringJob`, collects `input_path` + `marked_image_path` + `llm_input_path` + `result_json_path`, deletes the `AcceptedResult` row inside `transaction.atomic()`, then best-effort deletes the storage objects (log failures, do not raise). `ScoringJob` is NOT deleted.
- **`updated_at` on `AcceptedResult` requires touching the admin too.** `src/domains/vision/admin.py` currently exposes it read-only and annotates immutability; add `updated_at` to `readonly_fields` and refresh the docstring/notes so the admin stays honest.
- **Preview button uses the existing proxy path.** The row's Preview action resolves the marked-image URL as `/v1/scoring/jobs/${source_job}/marked-image` (the same path `_job_to_dto` sets, `services.py:624-637`) — no new image endpoint, no presigned URL.
- **Modify modal fetches the accepted snapshot, not the detector output.** It calls the new `GET /v1/scores/{result_id}` (returns `AcceptedResultDTO` with the accepted/corrected `holes`), NOT `getScoringJob(source_job)` (which would return the raw CV detector output and clobber a previous correction).
- **`AggregationDTO.recent` (home) now comes from the new list endpoint, page_size=20.** The home `Dashboard.tsx` switches its "Recent results" data source from `getAggregations().recent` (capped 10) to the new `getScores({page:1, page_size:20})`. The hero stats + chart still use `getAggregations()`.

## Prerequisites

Before any phase, amend the PRD constraint that the Modify half of this change reverses:

**Edit `context/foundation/prd.md` FR-010** (L97-99): the Socrates resolution currently reads *"kept; the accept/reject review step is the safety net; editing saved results is v2."* Update it to promote editing saved results from v2 to in-scope, e.g.:

> Resolution: kept; the accept/reject review step is the safety net. Editing saved results is in scope as of the `user-score-dashboard` change (PATCH `/v1/scores/{id}` recomputes the average; audit trail via `updated_at`).

This keeps the PRD (AGENTS.md §2 source of truth for domain constraints) honest with the code change; the model/admin docstring updates in Phase 1 then reflect an amended constraint rather than silently contradicting it. Sign-off on the PRD edit is the gate that unblocks Phase 2.

---

## Phase 1: Backend — Read APIs + migration

### Overview

Establish the data contract the frontend consumes: a paginated list and a detail read of `AcceptedResult`, plus the `updated_at` field that Phase 2's UPDATE needs. No mutation yet.

### Changes Required:

#### 1.1 Add `updated_at` to `AcceptedResult`

**File**: `src/domains/vision/models.py`

**Intent**: Lift the documented immutability invariant in preparation for Phase 2's UPDATE. Add a timestamp that records the last modification.

**Contract**: Add `updated_at = models.DateTimeField(auto_now=True)` to `AcceptedResult` (alongside `created_at` at `models.py:113`). Update the class docstring (`models.py:96-98`) to state the row is now mutable via `PATCH /v1/scores/{id}` and that `updated_at` tracks the last PATCH; `created_at` still records acceptance.

#### 1.2 Migration for `updated_at`

**File**: `src/domains/vision/migrations/0006_acceptedresult_updated_at.py` (new; next number after 0005)

**Intent**: Persist the new field.

**Contract**: Standard `AddField` migration for `AcceptedResult.updated_at` (`auto_now=True`). Generated via `make makemigrations vision`. Backfill is automatic (Django sets `auto_now` on existing rows at write time; no data migration needed for reads).

#### 1.3 Update admin to expose `updated_at`

**File**: `src/domains/vision/admin.py`

**Intent**: Keep the admin honest after the immutability lift.

**Contract**: Add `updated_at` to `AcceptedResultAdmin.readonly_fields` and `list_display` (alongside the existing fields at `admin.py:14-16,59-66`); refresh the admin docstring/notes that currently claim immutability to state the row is mutable via the PATCH route and `updated_at` records the last modification.

#### 1.4 List DTOs — paginated response

**File**: `src/domains/vision/dtos.py`

**Intent**: Define the wire contract for the paginated list endpoint.

**Contract**: Add a paginated response DTO mirroring `AdminUserListOut` (`src/domains/identity/dtos.py:119-130`), typed over the existing `ResultSummaryDTO`:
```python
class ScoreListOut(Model):
    items: list[ResultSummaryDTO]
    page: int
    page_size: int
    total: int
    total_pages: int
```
No change to `ResultSummaryDTO` itself — it already carries `result_id, source_job, created_at, score_average, hole_count, target_type` (`dtos.py:105-113`), which is exactly the row shape.

#### 1.5 Vision service — `list_results` (paginated)

**File**: `src/domains/vision/services.py`

**Intent**: Add the per-user paginated list, mirroring `list_users_for_owner`.

**Contract**: New function `list_results(*, user_uuid, page=1, page_size=20) -> ScoreListOut`. Implementation follows `list_users_for_owner` (`identity/services.py:293-362`) exactly:
- Query `AcceptedResult.objects.filter(user_uuid=user_uuid).order_by("-created_at")`.
- Clamp `page = max(1, page)`, `page_size = max(1, min(page_size, 50))`.
- `offset = (page - 1) * page_size`; slice `qs[offset : offset + page_size]`.
- `total = qs.count()`, `total_pages = (total + page_size - 1) // page_size`.
- Map each row to `ResultSummaryDTO` (reuse the mapping already done inside `aggregate_for_user` at `services.py:536` — extract or re-derive `hole_count = len(r.holes)`).
Return `ScoreListOut(items=..., page=..., page_size=..., total=..., total_pages=...)`. One class/function per file is already the convention here (`services.py` holds the domain functions); no new file.

#### 1.6 Vision service — `get_result` (detail)

**File**: `src/domains/vision/services.py`

**Intent**: Provide the per-hole accepted snapshot the Modify modal needs (the accepted/corrected scores, not the raw detector output).

**Contract**: New function `get_result(*, result_id, user_uuid) -> AcceptedResultDTO` mirroring `get_job` (`services.py:305-322`):
```python
def get_result(*, result_id, user_uuid) -> AcceptedResultDTO:
    try:
        ar = AcceptedResult.objects.get(id=result_id)
    except AcceptedResult.DoesNotExist as exc:
        raise PermissionError(...) from exc
    if ar.user_uuid != user_uuid:
        raise PermissionError(...)
    return _accepted_result_to_dto(ar)
```
Add a small `_accepted_result_to_dto(ar)` helper mapping the model to `AcceptedResultDTO` (the `holes` JSON → `list[DetectedHoleDTO]`, mirroring the inverse of the `accept_job` payload at `services.py:399-403`). `PermissionError` for both missing and not-mine (the BFF maps to 404).

#### 1.7 BFF routes — `GET /v1/scores` (list) + `GET /v1/scores/{result_id}` (detail)

**File**: `src/bff/routers/scoring_routes.py`

**Intent**: Expose the two read endpoints under the `/v1/scores` resource.

**Contract**: Two new routes on the existing `scoring_router` (mounted flat under `/v1/`, `bff/urls.py:30-32,42`):

`GET /v1/scores` — query params `page: int = 1, page_size: int = 20`; `auth=session_auth`; `response={200: ScoreListOut}`. Body mirrors the existing routes (`scoring_routes.py:108-111`): resolve `user_dto = get_user_context(str(request.user.sub))` (401 on missing user), then `return list_results(user_uuid=user_dto.user_uuid, page=page, page_size=page_size)`.

`GET /v1/scores/{result_id}` — path param `result_id: UUID`; `auth=session_auth`; `response={200: AcceptedResultDTO}`. Resolve identity, then:
```python
try:
    return get_result(result_id=result_id, user_uuid=user_dto.user_uuid)
except PermissionError:
    raise HttpError(404, "Not found") from None
```
Place these adjacent to `get_aggregations` (`scoring_routes.py:272-292`), and **mind the registration order**: django-ninja matches in declaration order, so the existing literal `GET /scores/aggregations` (currently at `scoring_routes.py:272-274`) MUST stay registered BEFORE the new parametrised `GET /scores/{result_id}` — otherwise `/v1/scores/aggregations` matches the UUID param route and fails parsing (422), breaking the home Dashboard's aggregation call. Safe order: `/scores` (list) → `/scores/aggregations` (literal) → `/scores/{result_id}` (param). The bare `/scores` list route is collision-free. Update the resource-naming docstring near `scoring_routes.py:276-282` to note the list/detail routes join the `/v1/scores` resource.

### Success Criteria:

#### Automated Verification:

- Migration applies cleanly: `make migrate` (and `make makemigrations vision` produces no unexpected diffs after 1.2).
- Domain + BFF unit/integration tests pass: `uv run pytest src/domains/vision/tests tests/system` (new tests added per the Testing Strategy below).
- Lint + type-check + import contracts: `make check`.

#### Manual Verification:

- `GET /v1/scores?page=1&page_size=20` returns the current user's scores, most-recent first, with correct `total`/`total_pages`.
- `GET /v1/scores/{id}` returns the full accepted result including `holes`.
- A `GET /v1/scores/{id}` for another user's result returns 404 (not 403).
- The admin shows `updated_at` on `AcceptedResult`.

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to Phase 2.

---

## Phase 2: Backend — UPDATE + DELETE

### Overview

Add the mutate endpoints: PATCH (Modify) which lifts immutability and recomputes `score_average`, and DELETE which removes the row + best-effort deletes the S3 objects. Adds the missing `ScoringStorage` delete methods.

### Changes Required:

#### 2.1 `ScoringStorage.delete_upload`

**File**: `src/domains/vision/pipeline/storage.py`

**Intent**: Provide the storage-object deletion surface that has never existed; delete the original upload object.

**Contract**: New method `delete_upload(self, stored_path: str) -> None`. Route through `_safe_key` (L90-120) so the `uploads/` namespace guard applies under S3. Implementation: `self._storage.delete(self._safe_key(stored_path))` (both FS and S3 backends expose `.delete(name)`). Swallow/raise strategy is the caller's responsibility (the delete service calls this best-effort) — keep this method strict (let exceptions propagate) so the caller can decide. Guard against empty/None `stored_path` (no-op).

#### 2.2 `ScoringStorage.delete_paths`

**File**: `src/domains/vision/pipeline/storage.py`

**Intent**: Delete a caller-supplied list of concrete storage objects (the original upload + a job's deliverables) in one call.

**Contract**: New method `delete_paths(self, paths: list[str]) -> None`. The caller (the `delete_result` service, §2.4) collects the concrete paths from the `ScoringJob` row (`input_path`, `marked_image_path`, `llm_input_path`, `result_json_path`) and passes them in — no `listdir` / prefix listing is needed (the `ScoringJob` already holds every key, and the file stem is not stored elsewhere so name derivation would be fragile). Implementation: `for p in paths: if p: self._storage.delete(self._safe_key(p))` — routing each through `_safe_key` (L90-120) so the `uploads/` / `jobs/` namespace guard applies under both backends. Both FS (`FileSystemStorage.delete`) and S3 (django-storages) expose `.delete(name)` per-key. Under FS, empty parent dirs may remain (acceptable, matches existing posture). Let exceptions propagate — the caller (`delete_result`) wraps this in best-effort try/except.

#### 2.3 Vision service — `update_result`

**File**: `src/domains/vision/services.py`

**Intent**: Lift immutability for the Modify flow; persist edited holes and recompute the average.

**Contract**: New function `update_result(*, result_id, user_uuid, holes: list[DetectedHoleDTO], target_type=None, caliber_hint=None, distance=None, weapon_type=None) -> AcceptedResultDTO`. Mirror `get_result`'s ownership check first (`AcceptedResult.objects.get` → `PermissionError` on missing; `ar.user_uuid != user_uuid → PermissionError`). Inside `@transaction.atomic` (or an atomic block in the BFF — pick one place; prefer the service to keep the BFF thin, matching `accept_job` which owns its atomic at `services.py:377`):
- Write `ar.holes = [{...hole dict...}]` (same shape `accept_job` writes at `services.py:399-403`).
- Recompute `ar.score_average = sum(h.score for h in holes) / len(holes)` (same formula as `services.py:404`).
- Apply the optional param overrides (`target_type`, `caliber_hint`, `distance`, `weapon_type`) when provided.
- `ar.save()` (`updated_at` is set by `auto_now=True`).
- Return `_accepted_result_to_dto(ar)` (the helper from 1.6).

#### 2.4 Vision service — `delete_result`

**File**: `src/domains/vision/services.py`

**Intent**: Hard-delete the `AcceptedResult` row + best-effort remove its storage objects; retain the `ScoringJob` audit row.

**Contract**: New function `delete_result(*, result_id, user_uuid) -> None`. Steps:
1. Owner-check: fetch the `AcceptedResult` (raise `PermissionError` on missing or `ar.user_uuid != user_uuid`).
2. Resolve `job = ScoringJob.objects.get(id=ar.source_job)` (best-effort: if the `ScoringJob` is already gone, skip storage deletion — log and proceed to delete the row).
3. Collect `paths_to_delete = [job.input_path]` plus any non-null deliverable paths among `[job.marked_image_path, job.llm_input_path, job.result_json_path]`.
4. Inside `transaction.atomic()`: `ar.delete()` (the `AcceptedResult` row only).
5. After commit, best-effort: `storage = ScoringStorage(); for p in paths_to_delete: try: storage.delete_paths([p]) / storage.delete_upload(p) except Exception: log.warning(...)`. Do NOT raise — the DB is the source of truth; orphaned S3 objects are a future sweeper's concern (matches `create_scoring_job`'s accepted orphan posture).

`ScoringJob` is NOT deleted. Use the typed-exception convention (`PermissionError`) for missing/not-mine so the BFF maps to 404.

#### 2.5 BFF routes — `PATCH /v1/scores/{result_id}` + `DELETE /v1/scores/{result_id}`

**File**: `src/bff/routers/scoring_routes.py`

**Intent**: Expose the Modify (UPDATE) and Delete mutations under `/v1/scores`.

**Contract**: Two new routes on `scoring_router`:

Request DTO for PATCH (define inline or in `scoring_routes.py` near `AcceptResultIn` at L208-223, mirroring its shape minus `job_id`):
```python
class ScoreUpdateIn(Model):
    holes: list[DetectedHoleDTO] = Field(min_length=1)  # guard the mean in update_result (mirrors AcceptResultIn.holes, scoring_routes.py:223)
    target_type: Optional[str] = None
    caliber_hint: Optional[str] = None
    distance: Optional[int] = None
    weapon_type: Optional[str] = None
```

`PATCH /v1/scores/{result_id}` — `auth=session_auth`, `response={200: AcceptedResultDTO}`, `@transaction.atomic`. Resolve identity; call `update_result(...)`; map `PermissionError → HttpError(404, "Not found")`. Send CSRF via `jsonHeaders()` on the client.

`DELETE /v1/scores/{result_id}` — `auth=session_auth`, `response={204: None}`. Resolve identity; call `delete_result(...)`; map `PermissionError → HttpError(404, "Not found")`; `return 204, None`. Mirror `delete_a_user` (`src/bff/routers/owner_routes.py:100-118`).

### Success Criteria:

#### Automated Verification:

- `uv run pytest src/domains/vision/tests tests/system` — new unit + system tests for `update_result`, `delete_result`, `ScoringStorage.delete_*` (FS + S3-fake per `test_storage_swap.py:248-253`).
- `make check` (lint + type-check + import contracts).

#### Manual Verification:

- PATCH a score's holes → the row's `score_average` reflects the new mean, `updated_at` advances, `created_at` is unchanged.
- DELETE a score → the row is gone from `GET /v1/scores`; under FS dev (`USE_S3=False`) the upload file and the `jobs/{id}/` deliverable files under `MEDIA_ROOT` are gone; the `ScoringJob` row still exists.
- PATCH/DELETE another user's result_id → 404.
- DELETE a result whose `ScoringJob` was already removed → row still deletes (no 500), S3 step skipped with a warning log.

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to Phase 3.

---

## Phase 3: Frontend — Shared list/row + read-only dashboard

### Overview

Build the shared `<ScoreRow>`/`<ScoreList>` (day-bucketed, action buttons present but not yet wired to modals), the read-only `/scores` dashboard page with pagination, the "Score dashboard" menu entry + route, and switch the home "Recent results" to the new shared component (page_size=20).

### Changes Required:

#### 3.1 API client — `getScores` + `getScore`

**File**: `src/frontend/src/api.ts`

**Intent**: Add the typed fetch wrappers the new components call.

**Contract**: Mirror `getAdminUsers` (`api.ts:276-291`) and `AdminUserList` (`api.ts:259-265`):
- `getScores({page=1, page_size=20})` → `GET /v1/scores?page=&page_size=` via `URLSearchParams`; returns `ScoreList` (`{items: ResultSummary[], page, page_size, total, total_pages}`). Throws `HttpError` (`api.ts:267-274`) on non-ok.
- `getScore(result_id: string)` → `GET /v1/scores/{result_id}`; returns `AcceptedResult` (the existing type at `api.ts:164-182` if it matches `AcceptedResultDTO`, else add/align it).
- (Phase 4 will add `updateScore` and `deleteScore` here.)

#### 3.2 `<ScoreRow>` component

**File**: `src/frontend/src/components/ScoreRow.tsx` (new) + `ScoreRow.module.css`

**Intent**: The single reusable row used by both the dashboard and home "Recent results".

**Contract**: Props `{ row: ResultSummary, onPreview: (row) => void, onModify: (row) => void, onDelete: (row) => void }`. Renders a `<li>` (mirroring `ResultsList.tsx:24-31` and the `AdminUsersPage` row layout `AdminUsersPage.module.css:36-49`):
- Left: `<span class={styles.date}>{row.created_at.slice(0,10)}</span>` and `<span class={styles.score}>{row.score_average.toFixed(1)}</span>` (bolded, `font-weight:600`, mirroring `ResultsList.module.css:50-54`).
- Right: three buttons — Preview (with the immutable `target.svg` as `<img src={targetIcon} alt="" />`), Modify, Delete — `type="button"`, each calling its `on*` prop. Mirror `AdminUsersPage`'s `.actionBtn`/`.dangerBtn` classes (`AdminUsersPage.module.css:97-114`).
Import `targetIcon` from `../assets/target.svg` (Vite handles SVG imports). Do NOT modify `target.svg`.

#### 3.3 `<ScoreList>` component

**File**: `src/frontend/src/components/ScoreList.tsx` (new) + `ScoreList.module.css`

**Intent**: Render rows grouped under day headers; shared by dashboard and home.

**Contract**: Props `{ rows: ResultSummary[], onPreview, onModify, onDelete }`. Compute day groups client-side: iterate `rows` (already most-recent-first from the API), bucket by `row.created_at.slice(0,10)` (YYYY-MM-DD), render each group as `<section><h3 class={styles.dayHeader}>{date}</h3><ul>...<ScoreRow/>...</ul></section>`. No collapse — rows within a day are listed individually under the header. Empty state: a `<p>No scores yet.</p>` when `rows.length === 0`.

#### 3.4 `<ScoreDashboard>` page

**File**: `src/frontend/src/components/ScoreDashboard.tsx` (new) + `ScoreDashboard.module.css`

**Intent**: The read-only (this phase) dashboard page at `/scores`.

**Contract**: Component holds `useState` for `{rows, page, pageSize, total, totalPages, loading, error}` (mirror `AdminUsersPage.tsx:27-31`). On mount and on `page`/`pageSize` change, call `getScores({page, page_size: pageSize})`; set state from the response. Render:
- A page-size `<select>` dropdown with options 10/20/30/50 (default 20), `onChange` resets `page=1` and updates `pageSize`.
- `<ScoreList rows={rows} onPreview={...} onModified={...} onDeleted={...} />` — the `on*` handlers are stubs in this phase (e.g. `() => {}` or `console.log`); Phase 4 wires them to the modals. (Note from §4.3 triage: Phase 4 moves the modal state *into* `<ScoreRow>`, so the row's mutate props become success callbacks `onModified`/`onDeleted`, not `onModify`/`onDelete`. Use those names already in Phase 3 so Phase 4 doesn't rename them. The `<ScoreRow>` itself renders the modals in Phase 4.)
- Pagination controls (Prev / "Page N of M" / Next), disabled at the bounds, mirroring `AdminUsersPage`'s pagination UI.
- Loading and error states.
The `onPreview`/`onModify`/`onDelete` props are passed through but no-ops until Phase 4.

#### 3.5 Route + menu entry

**File**: `src/frontend/src/components/AppShell.tsx`, `src/frontend/src/components/Sidebar.tsx`

**Intent**: Make the page reachable; add the menu entry under "Home".

**Contract**:
- `AppShell.tsx`: import `ScoreDashboard`; add `<Route path="/scores" element={<ScoreDashboard />} />` in the `<Routes>` tree (after the `/dashboard` route, `AppShell.tsx:46-57`).
- `Sidebar.tsx`: inside `topItems` (L31-40), insert a `<Link to="/scores">` immediately after the Home button, e.g. `<Link role="menuitem" className={styles.item} to="/scores">{collapsed ? '🎯' : 'Score dashboard'}</Link>` (mirror the Admin `<Link>` shape at L36-38). See Phase 4 for the `NavLink` active-styling conversion (this phase adds a plain `<Link>` so the route is reachable; 4.4 converts Home/Score dashboard/Admin to `NavLink` together).

#### 3.6 Switch home "Recent results" to the shared component

**File**: `src/frontend/src/components/Dashboard.tsx`

**Intent**: Home "Recent results" reuses `<ScoreList>` with max 20 rows from the new endpoint.

**Contract**: Replace the `<ResultsList recent={...} />` usage at `Dashboard.tsx:71-73` with `<ScoreList rows={recentRows} onPreview={...} onModified={...} onDeleted={...} />`. Data source: add a `useEffect` that calls `getScores({page:1, page_size:20})` and stores `recentRows` in state (keep the existing `getAggregations()` call for hero stats + chart — only the list source changes). The `on*` handlers are stubs in this phase (Phase 4 wires them; from home they open the same modals). **Remove `<ResultsList>` and delete the now-dead files:** `ResultsList.tsx` has exactly one caller (`Dashboard.tsx:14,72` — this step's replacement), so after the switch it is guaranteed dead code. Delete `src/frontend/src/components/ResultsList.tsx` and `src/frontend/src/components/ResultsList.module.css` in this step (don't leave it for a "follow-up").

### Success Criteria:

#### Automated Verification:

- Frontend tests pass: `make fe-test` (Vitest). Add component tests for `<ScoreRow>`, `<ScoreList>` (day-bucketing), and `<ScoreDashboard>` (pagination dropdown state).
- `make check` (lint + type-check, frontend side).

#### Manual Verification:

- Click "Score dashboard" in the menu → the `/scores` page loads, shows the user's scores grouped under day headers, most-recent first.
- Changing the page-size dropdown (10/20/30/50) re-fetches and re-renders; Prev/Next navigate pages.
- Home page "Recent results" shows up to 20 rows using the same row markup as the dashboard.
- The Preview/Modify/Delete buttons are visible on each row (actions wired in Phase 4).

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to Phase 4.

---

## Phase 4: Frontend — Actions (Preview, Modify modal, Delete modal) + NavLink

### Overview

Wire the three row actions: Preview (inline image + per-shot scores), Modify (fresh modal mirroring `BanModal`, PATCH on submit, onClose cancel), Delete (confirm modal, DELETE on confirm). Convert Home / Score dashboard / Admin menu entries to `NavLink` with shared active styling.

### Changes Required:

#### 4.1 Preview action

**File**: `src/frontend/src/components/ScoreDashboard.tsx` (and `Dashboard.tsx` for the home variant), plus a small `<ScorePreview>` presentational piece.

**Intent**: Show the target image with the per-shot scores in one line below, immutable.

**Contract**: A new `<ScorePreview>` (e.g. `src/frontend/src/components/ScorePreview.tsx`) that takes `result_id` (or pre-fetched data) and renders: `<img src={\`/v1/scoring/jobs/${source_job}/marked-image\`} alt="Marked target" />` (reuse the existing proxy path — `source_job` comes from the row) and a single line below listing the per-shot scores, e.g. `Scores: 10, 9, 9, X, …` rendered from `AcceptedResult.holes` (fetched via `getScore(result_id)` when the preview opens, since `ResultSummary` has no holes). The preview is immutable (no selects). The `onPreview` handler in the dashboard/home opens this preview inline (e.g. an expandable region under the row) or as a lightweight non-modal reveal — pick inline-expand to keep it simple; the per-shot scores render as a read-only single line below the image. The Preview button toggles the inline reveal for that row.

#### 4.2 API client — `updateScore` + `deleteScore`

**File**: `src/frontend/src/api.ts`

**Intent**: Add the mutate fetch wrappers.

**Contract**:
- `updateScore(result_id, payload)` → `PATCH /v1/scores/{result_id}` with JSON body `{holes, target_type?, caliber_hint?, distance?, weapon_type?}`; headers via `jsonHeaders()` (`api.ts:33-38`, sends CSRF); returns the updated `AcceptedResult`. Throws `HttpError` on non-ok.
- `deleteScore(result_id)` → `DELETE /v1/scores/{result_id}`; headers via `jsonHeaders()`; returns nothing (204). Throws `HttpError` on non-ok.

#### 4.3 `<ModifyModal>` + `<DeleteModal>`

**File**: `src/frontend/src/components/ModifyModal.tsx` (new) + `ModifyModal.module.css`; `src/frontend/src/components/DeleteModal.tsx` (new) + `DeleteModal.module.css`

**Intent**: The Modify and Delete modals, wired from the row buttons via the same state pattern `AdminUsersPage` uses.

**Contract** — `<ModifyModal>`:
- Mirror `BanModal.tsx`'s shell: props `{ result: ResultSummary, onClose, onModified }` (the row object, mirroring `BanModal`'s `{user}` prop — not a bare `resultId`, and not a generic `onSuccess`); overlay + card + Esc-to-dismiss (unless pending) per `BanModal.tsx:29-35`.
- On open, fetch `getScore(result.result_id)` (the accepted snapshot — NOT `getScoringJob`); populate local state: `corrections` map + the four param selects (`targetType`, `caliber`, `distance`, `weaponType`), copying the editable controls from `Results.tsx:111-174` (image, hole list with `<select>` of `SCORE_OPTIONS`, param selects from `taxonomy.ts`). `buildCorrectedHoles()` (`Results.tsx:76-86`) is copied/local.
- Submit button labeled **"Modify"** (not "Accept"), `aria-label="Modify score"`; on submit call `updateScore(result.result_id, payload)`; on success call `onModified()` (parent refetches the list) then `onClose()`.
- Cancel button labeled **"Cancel"** (not "Reject"), `type="button"`, `onClick={onClose}` (no navigation — the user never left the dashboard).
- Inline error via `<p role="alert">`.

**Contract** — `<DeleteModal>`:
- Mirror `DeleteUserModal.tsx`: props `{ result: ResultSummary, onClose, onDeleted }` (mirroring `DeleteUserModal`'s `{user, onClose, onDeleted}` — not `{target, onClose, onSuccess}`); overlay + card + Esc-dismiss; a confirm question; Cancel button (`onClick={onClose}`); confirm button labeled **"Delete"** (or "Delete permanently", matching `DeleteUserModal`), on confirm call `deleteScore(result.result_id)`; on success `onDeleted()` (parent removes the row from state) then `onClose()`; inline error via `<p role="alert">`.

**Wiring** — mirror `AdminUsersPage.tsx` exactly, including the per-row state shape:
- The modal state is a per-row string union, NOT a top-level `{kind, row}` object. The actual precedent is `AdminUsersPage.tsx:24` (`type Modal = 'ban' | 'delete';`) with each `UserRow` holding `const [modal, setModal] = useState<Modal | null>(null);` at `AdminUsersPage.tsx:170`. So `<ScoreRow>` owns its own `const [modal, setModal] = useState<'modify' | 'delete' | null>(null)` (the row comes from the `ScoreRow` closure — no `row` field in state); the Modify/Delete buttons call `setModal('modify')` / `setModal('delete')` (cf. L210, L218); and the two `{modal === '...' && <...Modal .../>}` blocks render inside `<ScoreRow>` (cf. L225-244).
- This means `<ScoreRow>`'s props change from Phase 3's `{onPreview, onModify, onDelete}` to either (a) receiving the modal components as children/render-props, or (b) `<ScoreRow>` rendering the modals itself (preferred — matches `AdminUsersPage`'s `UserRow`). Pick (b): `<ScoreRow>` imports `ModifyModal`/`DeleteModal`, owns the modal state, and calls back up via `onModified`/`onDeleted` props so the parent (`ScoreDashboard` / home `Dashboard`) can refetch or remove the row. The Phase 3 stub `onModify`/`onDelete` props are therefore replaced by `onModified`/`onDeleted` success callbacks.
- `onModified` triggers a refetch of the list (parent); `onDeleted` removes the row from local state and decrements `total` (parent).

#### 4.4 NavLink active styling across the menu

**File**: `src/frontend/src/components/Sidebar.tsx`, `Sidebar.module.css`

**Intent**: The user can see which page is active.

**Contract**: Convert the Home button, the new Score dashboard link, and the Admin `<Link>` all to react-router's `<NavLink>` so `aria-current="page"` and the `.active` class are applied automatically. Home currently uses `<button onClick={onHome}>` — replace with `<NavLink to="/dashboard" end>` (the `end` prop avoids `/dashboard` matching `/scores`-style subtrees; react-router v6 `end` ensures exact-match semantics for the home route). For the collapsed state, keep the glyph text (`⌂`, `🎯`, `⚙`). Add a shared `.active` style to `Sidebar.module.css` (e.g. `font-weight:600; background: var(--color-primary); color: var(--color-bg)` — pick values consistent with the existing palette tokens in `styles.css:16-56`). Update the Sidebar props/usages: the `onHome` prop is no longer needed if Home becomes a `NavLink` (verify `AppShell.tsx:42`'s `onHome={() => navigate('/dashboard')}` wiring is removed accordingly).

### Success Criteria:

#### Automated Verification:

- `make fe-test` — add modal tests (Modify submits PATCH then calls `onModified`; Delete confirm submits DELETE then calls `onDeleted`; Cancel calls `onClose` with no API call) and a Preview test.
- `make check` (frontend lint + type-check).

#### Manual Verification:

- Preview a row → the marked target image renders (via the proxy URL) with the per-shot scores in one line below; toggling closes it.
- Modify a row → the modal opens with the accepted scores pre-filled; change a hole score; click Modify → the row's bolded score updates to the new mean; the modal closes.
- Modify Cancel → modal closes with no API call and no row change.
- Delete a row → confirm modal; confirm → row disappears from the list (and from home on next load); under FS dev the S3/FS objects are gone.
- The active menu entry highlights on `/scores`, `/dashboard`, and `/admin`.
- An attempt to Modify/Delete another user's score (synthetic) returns 404 and the modal shows an error.

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before declaring the change done.

---

## Testing Strategy

### Unit / Integration Tests (backend):

- `list_results` — pagination math (page/page_size clamp ≤50, offset slice, `total`/`total_pages`), per-user filter (only the requester's rows), `-created_at` ordering. Use `make_accepted_result` (`src/domains/vision/test_utils.py:17-59`) + `days_ago` (L62-66).
- `get_result` — happy path; `PermissionError` on unknown id; `PermissionError` on another user's id (the ID-prober invariant).
- `update_result` — `score_average` recomputed correctly from patched holes; `updated_at` advances, `created_at` unchanged; optional param overrides apply; `PermissionError` on unknown/not-mine; atomic (rollback on a mid-update error).
- `delete_result` — 204-equivalent (row gone); `ScoringJob` row retained; storage objects deleted when present; **storage failure does not raise** (best-effort: log and succeed); missing `ScoringJob` does not block row delete; `PermissionError` on unknown/not-mine.
- `ScoringStorage.delete_upload` / `delete_paths` — FS branch (`ScoringStorage(location=tmp_path/"bucket")`, assert file gone) and S3-fake branch (`ScoringStorage.__new__(ScoringStorage)` + `_is_s3=True`, mock `_storage.delete`) per `test_storage_swap.py:248-253`. Namespace guard: a malformed path is rejected by `_safe_key`.

### System Tests (BFF, REST + CSRF):

Mirror `tests/system/test_owner_routes.py` DELETE block (L248-325) helpers (`_login_as`, `_csrf`, `_delete`, plus a `_patch` and `_get` for the new routes). Use `override_settings(MEDIA_ROOT=str(tmp_path))` so `ScoringStorage` writes under pytest's `tmp_path`. Matrix:
- `GET /v1/scores` — 200 paginated; 401 anonymous; per-user isolation (user A doesn't see user B's rows); `page_size` clamp to 50; `total`/`total_pages` correct.
- `GET /v1/scores/{id}` — 200 happy; 404 unknown; 404 not-mine; 401 anonymous.
- `PATCH /v1/scores/{id}` — 200 happy (average recomputed); 404 unknown; 404 not-mine; 401 anonymous; 403 no-CSRF.
- `DELETE /v1/scores/{id}` — 204 happy + storage-gone (assert upload file + `jobs/<id>/` deliverables removed under `tmp_path`); 404 unknown; 404 not-mine; 401 anonymous; 403 no-CSRF.

### Frontend Tests (Vitest):

- `<ScoreRow>` — renders date + bolded score + three action buttons; clicking each calls the right `on*` prop.
- `<ScoreList>` — day-bucketing (rows under the correct `YYYY-MM-DD` header); empty state.
- `<ScoreDashboard>` — page-size dropdown changes `pageSize` and resets `page=1`; Prev/Next disabled at bounds; loading + error states.
- `<ModifyModal>` — pre-fills from `getScore`; Modify submits `updateScore` then calls `onModified` + `onClose`; Cancel calls `onClose` with no API call; inline error on failure.
- `<DeleteModal>` — confirm calls `deleteScore` then `onDeleted` + `onClose`; Cancel calls `onClose` with no API call.
- `<ScorePreview>` — renders the proxy image + per-shot scores line; immutable.

### Manual Testing Steps:

1. Log in, open the menu, confirm "Score dashboard" is under "Home" and highlights active on `/scores`.
2. `/scores` shows your scores grouped by day, most-recent first; change the dropdown (10/20/30/50) and confirm pagination.
3. Preview a row → image + per-shot scores line render.
4. Modify a row → change a hole score → Modify → bolded score updates; reopen Modify → the corrected value persists (`updated_at` advanced).
5. Modify Cancel → no change.
6. Delete a row → confirm → row disappears; under FS dev, confirm the upload + `jobs/<id>/` files under `MEDIA_ROOT` are gone; the `ScoringJob` row still exists in the admin.
7. Home page "Recent results" shows up to 20 rows using the same row markup; its Preview/Modify/Delete work identically.
8. (Negative) Synthesize another user's `result_id` → Modify/Delete return 404 with an inline error.

## Performance Considerations

- The list query is `AcceptedResult.objects.filter(user_uuid=user_uuid).order_by("-created_at")` with a `count()` — both hit the indexed `user_uuid` (`models.py:102`). At MVP/hobbyist scale this is fine; no DB aggregation changes needed.
- S3 deletion is best-effort and after-commit, so it does not extend the perceived DELETE latency for the DB operation; if S3 latency (Tigris cross-region) becomes visible, defer to a q2 task (out of scope here).
- Client-side day-bucketing in `<ScoreList>` operates on a single page of rows (≤50) — O(n) per render, negligible.
- No N+1: `ResultSummaryDTO` is derived straight from the `AcceptedResult` columns (the `holes` JSON is read for `hole_count = len(r.holes)` only, not expanded, mirroring `aggregate_for_user` at `services.py:536`).

## Migration Notes

- `0006_acceptedresult_updated_at.py` — `AddField` for `updated_at` (`auto_now=True`). Existing rows get a value on first write; reads work without backfill. No data migration.
- No destructive migration: no column is removed, no table dropped.
- The immutability stance change is documented in the model + admin docstrings; no application-level migration is needed (the data is the same shape, just mutable).
- Delete is hard-delete; no migration of "deleted" rows (they're gone). Aggregations recompute naturally from the surviving rows.

## References

- Research: `context/changes/user-score-dashboard/research.md`
- Score model: `src/domains/vision/models.py:75-121` (`AcceptedResult`); `:13-72` (`ScoringJob`, image paths).
- DTOs: `src/domains/vision/dtos.py:18-25,37-65,68-84,105-113,123-128`.
- Accept/edit form to copy controls from: `src/frontend/src/components/Results.tsx:111-194` (`handleAccept` L88-104, `handleReject` L106-109, `buildCorrectedHoles` L76-86, `SCORE_OPTIONS` L21).
- Pagination precedent: `src/domains/identity/services.py:293-362`; DTO `src/domains/identity/dtos.py:119-130`; route `src/bff/routers/owner_routes.py:41-50`.
- Delete precedent: `src/domains/identity/services.py:278-290`; route `src/bff/routers/owner_routes.py:100-118`.
- Storage: `src/domains/vision/pipeline/storage.py:17-219` (`_safe_key` L90-120, `save_upload` L122-134, `write_deliverable_bytes` L199-218).
- Preview proxy: `src/bff/routers/scoring_routes.py:173-205`; URL set at `src/domains/vision/services.py:624-637`.
- Frontend patterns: `src/frontend/src/components/AdminUsersPage.tsx:161-247` (row + modal wiring); `BanModal.tsx`, `DeleteUserModal.tsx` (modal shell); `Sidebar.tsx:31-40` (menu); `AppShell.tsx:46-57` (routes); `Dashboard.tsx:71-73` (home recent results); `api.ts:151-157,164-182,259-291`.
- Lessons: `context/foundation/lessons.md` (URIs name resources; one class per file; Railpack Django app name).
- Icon: `src/frontend/assets/target.svg` (immutable).

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Prerequisites: Amend PRD constraint (gate for Phase 2)

- [x] 0.1 Edit PRD FR-010 (`context/foundation/prd.md:97-99`) to promote "editing saved results" from v2 to in-scope, citing this change (`user-score-dashboard`); get sign-off — Phase 2 cannot start until this lands

### Phase 1: Backend — Read APIs + migration

#### Automated

- [x] 1.1 Add `updated_at` to `AcceptedResult` (`models.py`) + update docstring — d7bdb72
- [x] 1.2 Generate + apply migration `0006_acceptedresult_updated_at` — d7bdb72
- [x] 1.3 Expose `updated_at` in `AcceptedResultAdmin` + refresh admin notes — d7bdb72
- [x] 1.4 Add `ScoreListOut` paginated response DTO — d7bdb72
- [x] 1.5 Add vision service `list_results` (paginated) — d7bdb72
- [x] 1.6 Add vision service `get_result` (detail) — d7bdb72
- [x] 1.7 Add BFF routes `GET /v1/scores` + `GET /v1/scores/{id}` — d7bdb72
- [x] 1.8 Unit + system tests for list/detail (incl. 404-not-mine, pagination clamp) — d7bdb72
- [x] 1.9 `make check` + `make be-test` green — d7bdb72

#### Manual

- [x] 1.10 Verify `GET /v1/scores` pagination + ordering; `GET /v1/scores/{id}` detail; 404 on another user's id; admin shows `updated_at` — d7bdb72

### Phase 2: Backend — UPDATE + DELETE

#### Automated

- [x] 2.1 Add `ScoringStorage.delete_upload` — 7bc6d23
- [x] 2.2 Add `ScoringStorage.delete_paths` (deliverable deletion) — 7bc6d23
- [x] 2.3 Add vision service `update_result` (recompute `score_average`, atomic) — 7bc6d23
- [x] 2.4 Add vision service `delete_result` (DB-first atomic, best-effort S3, retain `ScoringJob`) — 7bc6d23
- [x] 2.5 Add BFF routes `PATCH /v1/scores/{id}` + `DELETE /v1/scores/{id}` — 7bc6d23
- [x] 2.6 Unit + system tests for update/delete + storage delete (FS + S3-fake); CSRF matrix — 7bc6d23
- [x] 2.7 `make check` + `make be-test` green — 7bc6d23

#### Manual

- [x] 2.8 Verify PATCH recomputes average + advances `updated_at`; DELETE removes row + FS files, keeps `ScoringJob`; 404 on another user's id; storage failure doesn't block row delete — 7bc6d23

### Phase 3: Frontend — Shared list/row + read-only dashboard

#### Automated

- [x] 3.1 Add `getScores` + `getScore` to `api.ts` — 25887a8
- [x] 3.2 Build `<ScoreRow>` (date + bolded score + Preview/Modify/Delete buttons) — 25887a8
- [x] 3.3 Build `<ScoreList>` (day-bucketed) — 25887a8
- [x] 3.4 Build `<ScoreDashboard>` page (pagination dropdown 10/20/30/50, Prev/Next) — 25887a8
- [x] 3.5 Add `/scores` route + "Score dashboard" menu entry (plain `<Link>` for now) — 25887a8
- [x] 3.6 Switch home "Recent results" to `<ScoreList>` (page_size=20) + delete now-dead `ResultsList.tsx`/`.module.css` — 25887a8
- [x] 3.7 Vitest tests for ScoreRow/ScoreList/ScoreDashboard — 25887a8
- [x] 3.8 `make check` + `make fe-test` green — 25887a8

#### Manual

- [x] 3.9 Verify `/scores` page (day groups, pagination dropdown, Prev/Next); home shows ≤20 rows with same markup; action buttons visible — 25887a8

### Phase 4: Frontend — Actions (Preview, Modify modal, Delete modal) + NavLink

#### Automated

- [x] 4.1 Build `<ScorePreview>` (proxy image + immutable per-shot scores line) — bcee7da
- [x] 4.2 Add `updateScore` + `deleteScore` to `api.ts` — bcee7da
- [x] 4.3 Build `<ModifyModal>` (mirrors `BanModal`, Modify=PATCH, Cancel=onClose) + `<DeleteModal>` (confirm=DELETE) — bcee7da
- [x] 4.4 Wire row buttons to modals — modal state lives per-row inside `<ScoreRow>` (`useState<'modify'|'delete'|null>`), not at page level; success callbacks `onModified` (refetch) / `onDeleted` (refetch) bubble to `ScoreDashboard` + `Dashboard` — bcee7da
- [x] 4.5 Convert Home/Score dashboard/Admin menu entries to `<NavLink>` with shared `.active` style — bcee7da
- [x] 4.6 Vitest tests for Modify/Delete modals + Preview + NavLink active state — bcee7da
- [x] 4.7 `make check` + `make fe-test` green — bcee7da

#### Manual

- [x] 4.8 Verify Preview (image + scores line); Modify (PATCH, average updates, corrected value persists); Modify Cancel (no-op); Delete (confirm, row + FS files gone, `ScoringJob` retained); active menu highlight; 404 on another user's id shows inline error — bcee7da

### Phase 5: E2E (Playwright) — CRUD on processed values + UI, offline

> User-added phase beyond the original plan. The manual click-through items in
> each phase's Manual section are replaced here by a single browser-driven E2E
> that exercises the full alteration scenario (CRUD on an accepted result's
> processed values) end-to-end, with Google AI Studio disabled (MockDetector +
> no GOOGLE_API_KEY → fully offline). This is the V-Model top tier: the app is
> a black box, the browser is a real user, assertions are on what the user sees.

#### Automated

- [x] 5.1 Add E2E test `tests/system/test_score_dashboard_e2e.py` — boots a prod-shape `runserver` (baked bundle via WhiteNoise + dev-auth-bypass + VISION_DETECTOR=mock, no GOOGLE_API_KEY), seeds a ScoringJob + AcceptedResult via `manage.py shell`, drives the `/scores` dashboard through Playwright: read (row renders 8.0) → Preview (proxy image + per-shot scores line) → Modify (change hole 8→10, assert average recomputes to 8.4) → Delete (confirm, assert row disappears + empty state); asserts no server traceback throughout — a1d2049

#### Manual

- [x] 5.2 E2E green is the manual click-through verifier — it covers list/preview/modify/delete on the assembled system offline — a1d2049
