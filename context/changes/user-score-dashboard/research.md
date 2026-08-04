---
date: 2026-08-04T00:00:00+02:00
researcher: ZCode (10x-research)
git_commit: f44e4951d7f3658a099a63b18003f40328a676dd
branch: feature/add-user-score-dashboard
repository: target-o-meter
topic: "User score dashboard — list/preview/modify/delete scores + left menu + Recent results"
tags: [research, codebase, vision, bff, frontend, storage, dashboard]
status: complete
last_updated: 2026-08-04
last_updated_by: ZCode (10x-research)
---

# Research: User score dashboard

**Date**: 2026-08-04 (CET)
**Researcher**: ZCode (10x-research)
**Git Commit**: `f44e4951d7f3658a099a63b18003f40328a676dd`
**Branch**: `feature/add-user-score-dashboard` (local; not pushed — references below are local `file:line`)
**Repository**: `target-o-meter`

## Research Question

Build a standalone user's dashboard to: review all scores (paginated, day-bucketed), preview an image with the per-shot scores, **modify** an existing score (reuse the accept/reject form, repurpose Accept→UPDATE, Reject→Cancel), and **hard-delete** a score (DB row + S3 image). Also add a "Score dashboard" entry under "Home" in the left menu, and a "Recent results" section (max 20) on the home page reusing the same row component.

## Summary

The "score record" the dashboard lists is the **`AcceptedResult`** model (vision domain, `src/domains/vision/models.py:75-121`). Per-shot 0–10/X scores live as a JSON array on its `holes` column; the bolded per-row total is the denormalized `score_average` FloatField. **`AcceptedResult` stores no image references** — the image lives on the sibling `ScoringJob` row, reached via the plain-UUID `source_job` field (no FK, per AGENTS.md §5). The preview image is served same-origin through an existing BFF proxy route (NOT a presigned S3 URL), so the dashboard can reuse that route as-is.

The accept/reject form to reuse for Modify is **`Results.tsx`** (`src/frontend/src/components/Results.tsx`), and it is **already fully editable** — the per-hole scores and all four params are `<select>` dropdowns bound to local state. There is **no Redux / React Query / RTK** anywhere; state is component-local `useState` + direct `fetch` calls in `src/frontend/src/api.ts`. Styling is plain CSS Modules.

Four things are **MISSING and genuinely new** (the load-bearing gaps):

1. **Paginated LIST endpoint** — the only "recent list" today is the `recent` field of `GET /v1/scores/aggregations`, capped at 10, no offset/total. There is **no `GET /v1/scores`** list. The house pagination convention to mirror lives in the identity domain (`list_users_for_owner`, page/page_size clamped ≤50, returns `total` + `total_pages`).
2. **UPDATE endpoint** — no PATCH/PUT exists on any score/result route. Worse, **`AcceptedResult` is documented "immutable after create"** (`models.py:96-98`, `admin.py:14-16`) — Modify breaks a documented invariant, so it needs an explicit design decision (and likely an `updated_at` migration + `score_average` recompute).
3. **DELETE endpoint + storage-delete surface** — no `DELETE /v1/scores/{id}` and **no delete method anywhere on `ScoringStorage`** (`storage.py`). S3/FS object removal is 100% new code.
4. **Shared score-row component** — the existing `ResultsList` row is read-only (date + bold score + hole count, links to `/results/:source_job`) and has no action buttons or day-bucketing. The dashboard spec needs a richer row (date + bolded score left, Preview/Modify/Delete right) that BOTH the new dashboard page and the home "Recent results" reuse.

One naming hazard to flag up front: the codebase already calls the home page `Dashboard` (route `/dashboard`, component `Dashboard.tsx`). The new "Score dashboard" must use a **distinct path** (e.g. `/scores` or `/score-dashboard`) to avoid clashing.

## Detailed Findings

### Data model — the "score record" is `AcceptedResult`

`AcceptedResult` (`src/domains/vision/models.py:75-121`), `db_table = vision_acceptedresult` (migration `src/domains/vision/migrations/0005_acceptedresult.py`):

| Field | Type | Notes |
|---|---|---|
| `id` | `UUIDField` PK | the row key for detail/update/delete |
| `user_uuid` | `UUIDField(db_index=True)` | identity link (plain UUID, **not** an FK — AGENTS.md §5) |
| `source_job` | `UUIDField` | the `ScoringJob.id` this was accepted from; **the only way to reach the image** |
| `target_type` | `CharField(32)` | |
| `caliber_hint` | `CharField(64, null=True)` | |
| `distance` | `PositiveIntegerField(null=True)` | meters |
| `weapon_type` | `CharField(32, null=True)` | |
| `holes` | `JSONField` | **the per-shot scores live here** |
| `score_average` | `FloatField` | **the bolded per-row total** (denormalized snapshot) |
| `created_at` | `DateTimeField(auto_now_add=True)` | ordering key for "most recent first" + day-bucketing |

`Meta.unique_together = ("source_job", "user_uuid")` (`models.py:118`) — one accepted result per (job, user). **There is no `updated_at`** (consistent with the immutability stance).

**Per-shot shape** (written at `src/domains/vision/services.py:399-403`):
```python
holes_payload = [
    {"x": h.x, "y": h.y, "score": h.score,
     "confidence": h.confidence, "caliber": h.caliber}
    for h in holes
]
```
`score` is an `int` 0–10 (X represented as 10 — see `Results.tsx:24-26` `scoreValue()`). `score_average` is computed once at accept time as the mean of hole scores (`services.py:404`); there is no DB aggregation keeping it in sync.

**Ordering & day-bucketing precedent** — `aggregate_for_user` already does exactly what the dashboard needs: `order_by("-created_at")` (`services.py:507`) and day grouping via `r.created_at.date()` (`services.py:524-528, 546-550`). Mirror these.

### `ScoringJob` holds the image references

`src/domains/vision/models.py:13-72`, `db_table = vision_scoringjob`:

- `input_path: CharField(512)` (`models.py:38`) — **original upload key**, shape `uploads/{sha1-16}{ext}`.
- `marked_image_path: CharField(512, null=True)` (`models.py:52`) — **the annotated preview image key**, shape `jobs/{job_id}/{stem}_marked.png`.
- `llm_input_path`, `result_json_path` (`models.py:51,53`) — other deliverables, same `jobs/{job_id}/…` shape.
- `user_uuid: UUIDField(db_index=True)`, `status`, `result` (JSON), timestamps incl. `updated_at` (`models.py:60`).

**To preview or delete images for a dashboard row, the service must take `AcceptedResult.source_job` → fetch the `ScoringJob` → read its paths.** There's a 1:1 (unique constraint), so unambiguous — but it's a cross-lifecycle join: today the `ScoringJob` is treated as an audit record that survives accept (`services.py:377-378`: "The `ScoringJob` row is NOT deleted").

### DTOs — `ResultSummaryDTO` is the row, `AcceptedResultDTO` is the detail

All in `src/domains/vision/dtos.py`:

- **`ResultSummaryDTO`** (`dtos.py:105-113`) — *"One row in the dashboard's recent-results list"* (verbatim docstring). Fields: `result_id, source_job, created_at, score_average, hole_count, target_type`. This is the element type of `AggregationDTO.recent` and the natural list-row contract. ⚠ It does **not** carry the `holes` array — the preview/modify flows need a separate detail read.
- **`AcceptedResultDTO`** (`dtos.py:68-84`) — the full accepted result with `holes: list[DetectedHoleDTO]` + `score_average`. Closest existing DTO for the preview/modify data load. ⚠ **No route returns it for reading today** — `POST /v1/scoring/results` returns it on accept, but there is no GET-by-id.
- **`DetectedHoleDTO`** (`dtos.py:18-25`) — `{ x:int, y:int, score:int, confidence:float, caliber?:str }`. The per-shot value rendered below the preview image.
- **`ScoringJobDTO`** (`dtos.py:37-65`) — read of a `ScoringJob`, includes `result: Optional[ScoringResultDTO]` (with `holes`) and `marked_image_url: Optional[str]` (the BFF proxy path).
- **`AggregationDTO`** (`dtos.py:123-128`) — `{ hero, recent: list[ResultSummaryDTO], daily_averages }`; returned by `GET /v1/scores/aggregations`.

Note the scale warning the DTOs themselves carry (`dtos.py:91-94`): the canonical scale is 0–10. A stale `mocks/dashboard.ts` fixture used 0–100 and different field names — do not regress to it.

### BFF routes — what exists, what's missing

All routers mount flat under `/v1/` (`src/bff/urls.py:30-32,42`). Scoring routes live in `src/bff/routers/scoring_routes.py`. Existing score/result routes:

| Method | Path | Response | Notes |
|---|---|---|---|
| GET | `/v1/scoring/jobs/{job_id}` | `ScoringJobDTO` | reads the **job** (+ holes + marked_image_url). Keyed on `ScoringJob.id`. |
| GET | `/v1/scoring/jobs/{job_id}/marked-image` | `image/png` bytes | the **preview-image proxy** — reuse as-is. |
| POST | `/v1/scoring/results` | `AcceptedResultDTO` (201 or 200) | **accept**; idempotent on `job_id` (re-POST returns the unchanged row). |
| GET | `/v1/scores/aggregations` | `AggregationDTO` | hero + `recent` (capped 10) + chart. |

**MISSING routes the dashboard needs** (all genuinely new):
- `GET /v1/scores` (or `/v1/scoring/results`) — **paginated list**, page/page_size, returns `{items: ResultSummaryDTO[], page, page_size, total, total_pages}`.
- `GET /v1/scores/{result_id}` — **detail read** of an `AcceptedResult` (returns `AcceptedResultDTO` with `holes`). Needed because `ResultSummaryDTO` has no holes and the only existing read is keyed on `source_job`.
- `PATCH /v1/scoring/results/{result_id}` (or `PUT`) — **UPDATE** holes/params; must recompute `score_average`.
- `DELETE /v1/scores/{result_id}` — **hard-delete** row + storage objects; returns 204.

**Resource-naming** (lessons.md "API endpoint URIs name resources, not actions"): plural nouns, no verbs, HTTP method carries the verb. `/v1/scores/…` is the established aggregate-result resource; `/v1/scoring/results` is the accept resource. Pick one namespace and keep it consistent — `/v1/scores` is recommended for the new list/detail/delete (resource-oriented), with the UPDATE living alongside accept at `/v1/scoring/results/{id}` OR all four consolidated under `/v1/scores`. **Decision for the plan to pin down.**

### Identity scoping pattern (uniform — mirror exactly)

Every scoring route starts with (`scoring_routes.py:108-111`, `161-164`, `189-192`, `244-247`, `287-290`):
```python
try:
    user_dto = get_user_context(str(request.user.sub))   # identity/services.py:123-131
except get_user_model().DoesNotExist:
    raise HttpError(401, "Session user no longer exists") from None
```
The domain then enforces ownership raising **`PermissionError`** for both "missing" and "not mine" (so ID-probers can't distinguish), and the BFF maps `PermissionError → 404` (NOT 403) — `scoring_routes.py:166-170, 194-197, 259-260`. Canonical accessor: `get_job` (`services.py:305-322`). Every new endpoint (list/detail/update/delete) must preserve this 404-not-403 semantics and the `filter(user_uuid=user_uuid)` / `ar.user_uuid != user_uuid` check. `AcceptedResult.user_uuid` is indexed, so per-user queries are cheap.

### Pagination precedent — mirror `list_users_for_owner`

No ninja `Pagination` class, no DRF `page_size` anywhere. The house pattern (the template to copy) is in the identity domain:
- **Service:** `list_users_for_owner(*, q="", page=1, page_size=_DEFAULT_PAGE_SIZE)` — `src/domains/identity/services.py:293-362`. Constants `_DEFAULT_PAGE_SIZE = 20`, `_MAX_PAGE_SIZE = 50` (`services.py:153-154`). Clamp: `page = max(1, page)`, `page_size = max(1, min(page_size, _MAX_PAGE_SIZE))` (`services.py:308-309`). Slice `qs[offset : offset + page_size]`, `offset = (page-1)*page_size` (`services.py:319-320`). Totals `total = qs.count()`, `total_pages = (total + page_size - 1) // page_size` (`services.py:317-318`).
- **Response DTO:** `AdminUserListOut` — `src/domains/identity/dtos.py:119-130` (`items, page, page_size, total, total_pages`).
- **Route:** `GET /v1/users?q=&page=&page_size=` — `src/bff/routers/owner_routes.py:41-50`.

So for the dashboard list: page/page_size query params clamped to ≤50, offset slice, response carries `total` + `total_pages`. The dropdown (10/20-default/30/50) maps cleanly onto `page_size`. Frontend client pattern to copy: `getAdminUsers` (`src/frontend/src/api.ts:276-291`) + `AdminUserList` type (`api.ts:259-265`).

### Storage / S3 — `ScoringStorage` has NO delete method

**Backend:** `src/domains/vision/pipeline/storage.py`, class `ScoringStorage` (L17-219). It is a **wrapper**, not a Django storage subclass; it holds `self._storage` = `FileSystemStorage` (debug, `USE_S3=False`) or django-storages `S3Storage` (prod, via `default_storage`). Env selection at `storage.py:25-64`; S3 creds at `settings.py:353-380`. `save_upload` (L122-134) does the SHA-1 digest bucketing → `uploads/{digest}{ext}`. `write_deliverable_bytes` (L199-218) → `jobs/{job_id}/{name}`. `_safe_key` / `_safe_join` (L90-120) validate the `uploads/` | `jobs/` namespace prefix — **any new delete method must route through them under S3.**

**Reads → URL is a BFF proxy, NOT presigned.** `ScoringJobDTO.marked_image_url` is set to `/v1/scoring/jobs/{job.id}/marked-image` in `_job_to_dto` (`services.py:624-637`) — deliberately, to avoid leaking `AWSAccessKeyId`/`Signature` in query strings and baking in an internal `minio:9000` host. The proxy route (`scoring_routes.py:173-205`) streams bytes via `ScoringStorage.read_deliverable_bytes`. **The dashboard preview reuses this proxy route as-is** — put the proxy path on `<img src>`.

**DELETE surface is entirely new.** Whole-codebase grep: the only `.delete()` calls are `User.delete()` (DB row, identity) and a `Schedule.delete()` in a migration. The single storage-object deletion in the repo is in a test's `finally` cleanup (`tests/system/test_detector_env_wiring.py:72`), reaching into `_storage` directly (private). **`ScoringStorage` needs new methods** — the natural shape:
- `delete_upload(stored_path)` — deletes the `uploads/{digest}{ext}` object.
- `delete_deliverable_dir(job_id)` (or `delete_job_artifacts(job)`) — deletes the `jobs/{job_id}/` deliverables (marked image, llm input, result json).
Both delegate to `self._storage.delete(name)` (exists on both backends), routed through `_safe_key`.

**Cross-domain transaction & partial-failure caveat.** AGENTS.md §6.2 mandates `@transaction.atomic()` for multi-domain BFF orchestration (example: `create_scoring_job`, `scoring_routes.py:92-145`). But S3 is not a 2PC participant. Existing posture: in `create_scoring_job` the S3 write runs **before** the atomic enqueue, so a rollback orphans the upload — an accepted leak (small + SHA-1-deduped). For DELETE the mirror question:
- **Delete DB row first (inside atomic), then best-effort S3 delete** → S3 failure leaves an orphan (recoverable via a sweeper; the dashboard is correct because the row is gone). **Recommended** — matches the existing "DB is the source of truth; S3 orphans are a sweeper problem" posture.
- Delete S3 first, then DB → DB failure leaves a row whose image is gone (broken proxy). Worse UX. Avoid.

Existing `delete_user` (`identity/services.py:278-290`) is the hard-delete precedent to mirror: typed domain exceptions (`UserNotFoundError`, `CannotModifyOwnerError`) → BFF maps to HTTP (404 / 409). For scores, `PermissionError → 404` is the established mapping.

**Async (django-q2) for deletion?** Probably not. The PRD's "Max 3 concurrent tasks" cap (`settings.py:280-293`, `Q2_WORKERS=3`, `bulk=1`) is about CV cost (~30s/image). An S3 `delete_object` is sub-second, and the existing owner-initiated mutations (`delete_user`, ban/unban) are **synchronous inside `transaction.atomic()`**. Follow `delete_user` (sync); only go async if S3 latency in the BFF proves bad. Async shape if needed: `async_task("src.domains.vision.services.delete_job_artifacts", str(job_id))` — but note it breaks atomicity (the task body runs after commit, so a partial S3 failure leaves orphans).

### Existing accept/reject form — `Results.tsx`, already editable, but a route page not a modal

**File:** `src/frontend/src/components/Results.tsx` (component `Results`, named export L28). Reads `jobId` from URL via `useParams` (L29). Route: `/results/:jobId` (`AppShell.tsx:46-57`).

**Already editable** — each hole renders a `<select>` of `SCORE_OPTIONS = ['X','10',…,'0']` (`Results.tsx:21`) bound to a `corrections` map (L31-41, 129-139). The four "Confirm parameters" selects (caliber/distance/weapon_type/target_type, L144-168) are also editable, sourced from `src/frontend/src/taxonomy.ts`. `buildCorrectedHoles()` (L76-86) produces the `AcceptedHole[]` payload. **No new input controls are needed for Modify.**

**Data source:** `getScoringJob(jobId)` (`api.ts:151-157`) → `GET /v1/scoring/jobs/{id}` → `ScoringJobDTO`. The `ResultSummary` dashboard row (`api.ts:190-197`) carries `result_id` + `source_job` but **no holes** — so opening Modify for a saved result needs either a new detail read keyed on `result_id`, or reuse `getScoringJob(source_job)`.

**Accept handler** (`Results.tsx:88-104`, `handleAccept`) calls `acceptResult(jobId, payload)` (`api.ts:210-227`) → `POST /v1/scoring/results`. **This is the action Modify must repurpose for UPDATE.** But the POST is idempotent-on-`job_id` and returns the **unchanged** row on re-POST (`scoring_routes.py:264-269`), so it cannot stand in for UPDATE — a new PATCH/PUT is required. Post-success `navigate('/dashboard')` is reusable verbatim.

**Reject handler** (`Results.tsx:106-109`, `handleReject`) is **non-destructive**: just `navigate('/dashboard')` (no API call, comment cites PRD FR-011). This is exactly the "Cancel returns to previous screen" behavior. In a modal, prefer `onClose()` (close, no navigation) per the modal pattern below.

**Buttons** (`Results.tsx:176-194`): `.accept` ("Accept", `aria-label="Accept result"`, `onClick={handleAccept}`) and `.reject` ("Reject", `aria-label="Reject result"`, `onClick={handleReject}`). For Modify: relabel "Accept"→"Modify", "Reject"→"Cancel" (and the `aria-label`s). Styles at `Results.module.css:107-141`.

**REUSE checklist (as-is):** the whole editable form body (image, holes list with selects, params selects, `buildCorrectedHoles`, error display) — `Results.tsx:111-174`; the `AcceptedHole`/`AcceptedResult` types + payload shape (`api.ts:164-182`); the navigate-away / `onClose()` cancel behavior; `jsonHeaders()` (`api.ts:33-38`).

**CHANGE checklist:** button labels + aria-labels; submit verb (swap POST-create for a new PATCH-update client + endpoint); form factor (`Results` is a route page, not a modal — extract its body into a presentational sub-component both the `/results/:jobId` route and a new `<ModifyModal>` render, OR build a fresh `ModifyModal` mirroring `BanModal`'s shell).

### Modal infrastructure — hand-rolled, no shared primitive

**No generic `<Modal>`.** Three sibling modals follow the same hand-rolled overlay/card pattern: `BanModal.tsx`, `DeleteUserModal.tsx`, `NickPrompt.tsx` (all in `src/frontend/src/components/`). `Results.tsx` is **not** modal — it's a route page.

**Reusable modal API (from `BanModal`/`DeleteUserModal`):** props `{ target, onClose, onSuccess }`; outer `<div className={styles.overlay} role="dialog" aria-label=… onClick={onClose}>`; inner `<form className={styles.card} onClick={(e)=>e.stopPropagation()} onSubmit={handleSubmit}>`; Esc-to-dismiss via a `useEffect` keydown listener that calls `onClose` unless `pending` (`BanModal.tsx:29-35`); a Cancel button (`type="button" onClick={onClose}`); inline error via `<p role="alert">`.

**Row→modal wiring pattern** (mirror for the dashboard): `AdminUsersPage.tsx`'s `UserRow` holds `const [modal, setModal] = useState<Modal|null>(null)` (L170); action buttons call `setModal('ban')` / `setModal('delete')` (L210, 218); conditional render (L225-244):
```tsx
{modal === 'delete' && (
  <DeleteUserModal target={user} onClose={() => setModal(null)}
    onDeleted={() => { onDeleted(user.sub); setModal(null); }} />
)}
```
This is the exact pattern the dashboard's Modify/Delete flows should reuse.

### Frontend layout — left menu, routing, home page

**Left menu:** `src/frontend/src/components/Sidebar.tsx`. **Inline JSX, not a data array.** Two groups: `styles.topItems` (top) and `styles.bottomItems`. The "Home" entry and neighbor verbatim (`Sidebar.tsx:31-40`):
```tsx
<div className={styles.topItems}>
  <button role="menuitem" className={styles.item} onClick={onHome}>
    {collapsed ? '⌂' : 'Home'}
  </button>
  {isOwner && (
    <Link role="menuitem" className={styles.item} to="/admin">
      {collapsed ? '⚙' : 'Admin'}
    </Link>
  )}
</div>
```
Icons are emoji/unicode glyphs toggled by `collapsed`. **There is no active-link styling today** (no `NavLink`, no `aria-current`). To add "Score dashboard" under Home: insert a `<Link to="/scores" className={styles.item}>{collapsed ? '🎯' : 'Score dashboard'}</Link>` inside `topItems` right after the Home button (mirroring the Admin `<Link>` shape). Sidebar props: `{ collapsed, onToggle, onLogout, onHome?, isOwner? }`.

**Routing:** `BrowserRouter` in `App.tsx:46`; single `<Routes>` tree in `AppShell.tsx:46-57`. Home serves at **both `/` and `/dashboard`** → `<Dashboard />`. Current routes: `/`, `/dashboard`, `/capture`, `/upload`, `/waiting/:jobId`, `/results/:jobId`, `/admin`. To add the page: `<Route path="/scores" element={<ScoreDashboard />} />` after line 48 + import alongside L16-21. Every route is wrapped by `AppShell` (`TopBar` + `Sidebar` + `<main>`, `AppShell.tsx:34-60`); no nested layout.

**Home page (`Dashboard.tsx`)** is a CSS grid with four regions (`Dashboard.tsx:50-79`): `<HeroStats>`, "Add photos" button, **`<ResultsList recent={...} />`** (L71-73 — the existing "Recent results"), `<DailyAverageChart>`. Data from `getAggregations()` on mount (L30-36); `aggregations.recent` (`ResultSummary[]`) feeds `ResultsList`.

**`ResultsList`** (`src/frontend/src/components/ResultsList.tsx:15-37`) is the existing read-only recent list. Row markup (L24-31):
```tsx
<li key={r.result_id} className={styles.row}>
  <Link to={`/results/${r.source_job}`} className={styles.link}>
    <span className={styles.date}>{r.created_at.slice(0,10)}</span>
    <span className={styles.score}>{r.score_average.toFixed(1)}</span>
    <span className={styles.count}>{r.hole_count} holes</span>
  </Link>
</li>
```
It does **not** match the dashboard spec (no action buttons, no day-bucketing). **A reusable score-row component with Preview/Modify/Delete actions and day grouping does NOT exist and must be built** — recommended: extract a shared `ScoreRow` used by BOTH the new dashboard page and the home "Recent results".

**Max-20 on home:** the backend caps `recent` at 10 (`aggregate_for_user(recent_limit=10)`, `services.py:475`); frontend does no client-side limit. To show up to 20 on home: either raise `recent_limit` (but the route doesn't expose it as a param today) or switch home to the new paginated list call sliced to 20.

**Styling:** plain CSS Modules (`.module.css` co-located per component) + global `src/frontend/src/styles.css` with palette tokens on `:root` (`--color-bg/fg/primary/muted/border`, semantic `--color-danger*/warning*/neutral*`). Mobile breakpoint convention `@media (max-width: 760px)`. For the day-bucketed sub-groups + per-row layout (date + bolded score left, actions right), mirror `AdminUsersPage.module.css` `.row` (flex, `align-items:center`, bordered card) + `.actions` (flex gap), and the bolded-score style at `ResultsList.module.css:50-54` (`.score { font-weight:600; font-size:1.25rem }`).

**i18n:** none. All labels are hardcoded inline strings (e.g. "Accept"/"Reject"/"Ban"/"Cancel"/"Delete permanently"). For "Modify"/"Cancel"/"Delete"/"Preview", hardcode inline — matching convention. (Existing "Reject"/"Accept" will need relabeling in the Modify path.)

### Tests & test_utils

AGENTS.md §5: *"System tests MUST NOT use factory_boy directly against domain models. Use `test_utils.py` or the REST API."*

- **Seeders:** `src/domains/vision/test_utils.py` — `make_accepted_result(...)` (L17-59) + `days_ago(n)` (L62-66). `src/domains/identity/test_utils.py` — `make_user`, `make_owner`, `make_ban`. These are the sanctioned ORM-touching layer.
- **System-test pattern (REST + CSRF):** mirror `tests/system/test_owner_routes.py` DELETE block (L248-325). Helpers `_login_as` (force_login), `_csrf` (GET `/` to seed cookie, read `client.cookies["csrftoken"].value`), `_delete` wrapper (`client.delete(..., HTTP_X_CSRFTOKEN=csrf)`). Markers `pytestmark = [pytest.mark.django_db, pytest.mark.dev]`.
- **FS in tests:** `override_settings(MEDIA_ROOT=str(tmp_path))` (e.g. `test_scoring_routes.py:110`) so `ScoringStorage` writes under pytest's `tmp_path`. Delete tests should assert the upload + `jobs/<uuid>/` dir are gone after DELETE.
- **Storage-swap unit test pattern:** `src/domains/vision/tests/test_storage_swap.py` constructs `ScoringStorage(location=tmp_path/"bucket")` for FS and `ScoringStorage.__new__(ScoringStorage)` + manual `_is_s3=True` for the S3-shaped fake (L248-253) — use for a new `delete_*` unit test covering both backends without real S3 creds.
- **Mutation-test matrix to mirror** (from `test_owner_routes.py` DELETE): 204 happy path + storage-gone side-effect, 404 unknown id, 404 not-owned-by-me (the ID-prober invariant), 401 anonymous, 403 no-CSRF.

## Code References

**Backend — models / DTOs / services / storage:**
- `src/domains/vision/models.py:75-121` — `AcceptedResult` (the dashboard row); `:96-98` the immutability docstring Modify contradicts.
- `src/domains/vision/models.py:13-72` — `ScoringJob` (holds `input_path` L38, `marked_image_path` L52, etc.).
- `src/domains/vision/dtos.py:18-25` — `DetectedHoleDTO`; `:37-65` `ScoringJobDTO`; `:68-84` `AcceptedResultDTO`; `:105-113` `ResultSummaryDTO` (the row); `:123-128` `AggregationDTO`.
- `src/domains/vision/services.py:305-341` — `get_job` / `get_job_for_user` (ownership pattern); `:344-448` `accept_job` (holes payload L399-403, score_average L404); `:472-566` `aggregate_for_user` (ordering + day-bucketing precedent; `recent_limit=10` L475); `:624-637` `_job_to_dto` (sets `marked_image_url` to proxy path).
- `src/domains/vision/pipeline/storage.py:17-219` — `ScoringStorage`; `:25-64` backend selection; `:90-120` `_safe_key`/`_safe_join`; `:122-134` `save_upload`; `:199-218` `write_deliverable_bytes`. **No delete method — to add.**
- `src/domains/identity/services.py:123-131` — `get_user_context`; `:278-290` `delete_user` (hard-delete precedent); `:293-362` `list_users_for_owner` (pagination precedent); `:153-154` page-size constants.
- `src/domains/identity/dtos.py:119-130` — `AdminUserListOut` (paginated response shape to mirror).

**Backend — BFF:**
- `src/bff/urls.py:30-32,42` — router mount + `/v1/` prefix.
- `src/bff/api.py:42-64` — `session_auth`, `require_owner`.
- `src/bff/routers/scoring_routes.py:92-145` `create_scoring_job`; `:148-170` `get_scoring_job` (404 mapping); `:173-205` `marked-image` proxy (reuse for preview); `:226-269` `accept_scoring_result` (POST, idempotent-on-job_id — cannot stand in for UPDATE); `:272-292` `get_aggregations`.
- `src/bff/routers/owner_routes.py:41-50` `list_users` (paginated route shape); `:100-118` `delete_a_user` (DELETE 204 shape + exception→HTTP mapping).

**Frontend:**
- `src/frontend/src/components/Results.tsx:28-205` — the accept/reject form (editable); `:88-104` `handleAccept`; `:106-109` `handleReject`; `:176-194` buttons; `:21` `SCORE_OPTIONS`; `:76-86` `buildCorrectedHoles`.
- `src/frontend/src/components/Results.module.css:107-141` — `.accept`/`.reject` button styles.
- `src/frontend/src/components/Sidebar.tsx:31-40` — menu topItems (Home insertion point).
- `src/frontend/src/components/AppShell.tsx:46-57` — route table (insert `/scores` route).
- `src/frontend/src/components/Dashboard.tsx:30-79` — home page; `:71-73` existing `<ResultsList>` "Recent results".
- `src/frontend/src/components/ResultsList.tsx:15-37` — existing read-only row (no actions, no day-bucketing).
- `src/frontend/src/components/AdminUsersPage.tsx:161-247` — row-with-action-buttons + row→modal wiring pattern (`:170` modal state, `:210,218` open, `:225-244` render); `AdminUsersPage.module.css:36-49` `.row`, `:92-95` `.actions`.
- `src/frontend/src/components/BanModal.tsx` / `DeleteUserModal.tsx` / `NickPrompt.tsx` — hand-rolled modal pattern (overlay + card + Esc-dismiss).
- `src/frontend/src/api.ts:33-38` `jsonHeaders`; `:151-157` `getScoringJob`; `:190-197` `ResultSummary`; `:210-227` `acceptResult` (POST create); `:229-235` `getAggregations`; `:259-265` `AdminUserList` (paginated type); `:276-291` `getAdminUsers` (paginated client pattern).
- `src/frontend/src/taxonomy.ts` — select option sources (calibers/distances/weapon_types/target_types).
- `src/frontend/src/styles.css:16-56` — CSS custom-property palette tokens.
- `src/frontend/assets/target.svg` — the preview-button icon (immutable per spec).

**Tests / seeders:**
- `src/domains/vision/test_utils.py:17-66` — `make_accepted_result`, `days_ago`.
- `src/domains/identity/test_utils.py:18-39` — `make_user`, `make_owner`.
- `tests/system/test_owner_routes.py:248-325` — DELETE test matrix to mirror.
- `tests/system/test_scoring_routes.py:110` — `override_settings(MEDIA_ROOT=tmp_path)` pattern.
- `src/domains/vision/tests/test_storage_swap.py:248-253` — FS/S3-fake swap for storage unit tests.

## Architecture Insights

1. **Immutability is the central tension.** `AcceptedResult` is documented immutable (`models.py:96-98`, `admin.py:14-16`, no `updated_at`). Modify is the one piece of this feature that contradicts a documented invariant — it is genuine new design, not a missing route. The plan must (a) decide explicitly to lift immutability, (b) add an `updated_at` migration, and (c) recompute `score_average` on update (since it's a denormalized snapshot, not a DB aggregation).
2. **Two-row data model for one logical "score".** The dashboard row (`AcceptedResult`) and the image (`ScoringJob`) are siblings joined by a plain UUID (`source_job`), not an FK. Every preview and every delete must resolve `source_job → ScoringJob`. This is by design (AGENTS.md §5 "No FK Across Domains"; `ScoringJob` is an audit record) but it makes delete's cleanup semantics a product decision: delete just the `AcceptedResult`? Also the `ScoringJob`? Also the storage objects? The spec says "hard delete the score from the database and remove the image from the S3 bucket" — that's `AcceptedResult` + storage objects; whether the `ScoringJob` row also goes is unspecified.
3. **DB is source of truth; S3 orphans are a sweeper problem.** The existing `create_scoring_job` already accepts S3-orphan-on-rollback leaks. Delete should mirror this: atomic DB delete first, best-effort S3 delete after, log/persist failures for a sweeper. S3 is not 2PC; do not try to make it transactional.
4. **No framework magic to fight.** No Redux/RTK/React Query, no pagination library, no shared Modal, no i18n, no icon library. Everything is `useState` + `fetch` + CSS Modules + hardcoded strings + emoji glyphs. The plan should follow these conventions (e.g. hand-rolled page/page_size pagination mirroring `list_users_for_owner`, a hand-rolled modal mirroring `BanModal`) rather than introduce a framework.
5. **Pagination convention already exists — in identity, not vision.** `list_users_for_owner` (page/page_size, clamp ≤50, `{items, page, page_size, total, total_pages}`) is the explicit template. Reuse it rather than inventing a limit/offset or cursor scheme.
6. **Preview image is a proxy, not a presigned URL.** Reuse `GET /v1/scoring/jobs/{id}/marked-image` as-is; do NOT generate presigned S3 URLs (the codebase deliberately avoids them — `services.py:624-637`).
7. **Naming collision: "Dashboard" is already the home page.** Use a distinct path (`/scores` recommended) and a distinct component name for the new page.

## Historical Context (from prior changes)

- `context/foundation/lessons.md` — "API endpoint URIs name resources, not actions": name new routes with plural nouns (e.g. `/v1/scores`, not `/v1/list-scores`); let HTTP method carry the verb. Directly governs the four new routes' naming.
- `context/foundation/lessons.md` — "One class per file, matching filename": if new domain service classes are introduced (e.g. a `ScoreResultUpdateService`), one class per file. Pure contract collections (`ports.py`, `dtos.py`) are the exception.
- `src/domains/vision/dtos.py:87-94` (inline comment) — the canonical dashboard DTOs are the 0–10-scale S-03 ones; an old `mocks/dashboard.ts` fixture used 0–100 and must not be regressed to.
- The `POST /v1/scoring/results` accept path (S-03) deliberately lives under `/v1/scoring/` while aggregation lives under `/v1/scores/` (`scoring_routes.py:276-282` docstring) — the plan should pick one namespace for the new CRUD and document why.

## Related Research

- None. This is the first `research.md` under `context/changes/`. (`AggregationDTO`/`ResultSummaryDTO` were introduced in an earlier archived change; their design rationale is inline in `dtos.py:87-128` rather than in a surviving research artifact.)

## Open Questions

These are decisions for `/10x-plan` to pin down (not blockers for research, but they shape the plan):

1. **Namespace for the new CRUD.** All four under `/v1/scores` (recommended — resource-oriented, matches the lessons.md rule), or split (list/detail/delete under `/v1/scores`, update alongside accept under `/v1/scoring/results/{id}`)?
2. **Detail read for the Modify form.** Add `GET /v1/scores/{result_id}` returning `AcceptedResultDTO` (clean, keyed on the row), or reuse `getScoringJob(source_job)` (works today but reads the CV job, not the accepted snapshot)?
3. **Modify form factor.** Extract `Results.tsx`'s body into a shared presentational component used by both the `/results/:jobId` route and a new `<ModifyModal>`, OR build a fresh `ModifyModal` mirroring `BanModal` + copying the editable controls? (Extraction is DRYer; fresh is lower-risk.)
4. **Delete scope.** "Hard delete the score + remove the image" — does that mean: delete `AcceptedResult` + storage objects only (leave `ScoringJob` as audit record, current posture), OR also delete the `ScoringJob` row? The spec doesn't say.
5. **Delete ordering / failure policy.** DB-first + best-effort S3 (recommended, matches existing orphan posture) — confirm, and decide whether a sweeper task / orphan-log is in scope for this change or deferred.
6. **Home "Recent results" max-20.** Raise `aggregate_for_user`'s `recent_limit` (and expose it as a route param), or switch home to the new paginated list endpoint sliced to 20?
7. **Active-link styling on the new menu entry.** The Sidebar has no active-state today; does adding "Score dashboard" warrant introducing `NavLink`/`aria-current` for it (and optionally retrofitting Home/Admin), or stay with the plain `<Link>` convention?
8. **`updated_at` migration for `AcceptedResult`.** Required to support Modify cleanly (and to track "modified" rows). Confirm scope.
