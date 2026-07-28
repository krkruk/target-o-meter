# S-03 accept-persist-dashboard Implementation Plan

## Overview

S-03 (`accept-persist-dashboard`) closes the US-01 vertical (auth → photograph →
detect → review → **accept → dashboard updates**) and is the `market-feedback`
validation milestone. It adds three things on top of the S-02 vision seam:

1. **Persistence + aggregation** (FR-009/010/011/012) — a new `AcceptedResult`
   sibling model, `POST /v1/scoring/results` (accept) + `GET /v1/scores/
   aggregations` (dashboard) BFF routes, and the dashboard swapped from mocked
   fixtures to real API calls.
2. **The prod enabler** — land the deferred OpenCV+S3 refactor in
   `vision/pipeline/storage.py` (tempfile download before `cv2.imread`,
   prefix-based containment, deliverable upload-back) so `VISION_DETECTOR=google`
   + `USE_S3=True` against Railway Tigris actually processes a real upload. This
   is the single S-02→S-03 deferred item that "modify little to none of the
   vision code" cannot avoid: OpenCV fundamentally needs local bytes.
3. **Parameter confirmation** (FR-009) — collect weapon_type (missing) +
   target_type selector (hardcoded today), and promote `distance` from a BFF
   mock field dropped on the floor to a real `ScoringJob` column.

The dev loop keeps `VISION_DETECTOR=mock` + `USE_S3=True` (MinIO); the
`MockDetector` is rewritten to emit random N holes (default 10, scores 0–10,
seeded for tests) so the accept→persist→aggregate round-trip exercises varied
data. The user has decided "70% fidelity is good enough" — recorded in the PRD
and roadmap; no detector-accuracy work lands here.

## Current State Analysis

**S-02 shipped the full vision seam + SPA.** The round-trip is real today:
`POST /v1/scoring/jobs` (multipart, `@transaction.atomic`) → q2 →
`process_image` (env-driven `DetectorFactory.build(VISION_DETECTOR)`, default
`"google"`) → `GET /v1/scoring/jobs/{job_id}` poll. Five SPA routes
(`/dashboard`, `/capture`, `/upload`, `/waiting/:jobId`, `/results/:jobId`)
work end-to-end against `MockDetector`. The Docker dev stack (web + worker +
MinIO + create-bucket) and prod-shape stack both boot.

**Five load-bearing gaps S-03 fills:**

1. **No persistence/aggregation concept anywhere.** `ScoringJob`
   (`src/domains/vision/models.py:13`) is the CV-pipeline record only — input
   path, q2 statuses, raw result JSON, deliverable paths. No accept/reject flag,
   no list endpoint, no aggregation. The dashboard reads from
   `src/frontend/src/mocks/dashboard.ts` (verified: `HeroStats.tsx:4`,
   `ResultsList.tsx:6`, `DailyAverageChart.tsx:17` all import fixtures).
2. **The S3 path raises `NotImplementedError`.** `ScoringStorage._safe_join`
   (`pipeline/storage.py:79-80`) and every path-shaped method that routes
   through it (`absolute_path`, `read_upload`, `write_deliverable`,
   `deliverable_dir`) raise under `USE_S3=True`. `process_image` calls
   `storage.absolute_path(job.input_path)` at `services.py:141` and
   `storage.deliverable_dir(...).mkdir(...)` at `:147` — both FS-only. Under
   prod `USE_S3=True` against Tigris, a real Google-detector upload fails
   immediately. **S-02 only proved the storage *config swap*; the byte-round-trip
   refactor is owed.**
3. **`distance_m` is a dead BFF field.** `ScoringJobIn.distance_m`
   (`scoring_routes.py:66`) is parsed and dropped — the docstring at `:22-24`
   says "promoted to a real `ScoringJob.distance` column in S-03 (FR-009)."
   `schedule_image_processing` (`services.py:70-97`) has no `distance` param.
4. **No weapon_type; `target_type` hardcoded.** `CaliberDistanceStep.tsx:8-11`
   collects `{caliber, distance_m}` only. Both wizard exits hardcode
   `target_type: 'air_pistol'` (`Capture.tsx:31`, `Upload.tsx:28`) with explicit
   `TODO(S-03)` markers. FR-009 requires confirming caliber, distance, AND weapon
   type.
5. **`MockDetector` is a fixed 5-hole pattern** (`mock_detector.py:29-35`). The
   user wants a dev mock emitting random N holes (default 10, scores 0–10).

**Patterns to follow (from S-02):**
- BFF routes: `session_auth` (401 anon) + body-call `get_user_context`
  (`scoring_routes.py:92-95`); `@transaction.atomic` on multi-domain writes
  (AGENTS.md §6.2); `PermissionError → HttpError(404)` for ownership
  (`scoring_routes.py:128-130`); the `try/except DoesNotExist → 401` wrap for
  deleted-between-auth-and-body sessions.
- Atomicity is two-layered and both stay (service's nested savepoint + BFF's
  outer transaction; q2 broker is SQLite so the task row rolls back with the
  outer transaction).
- DTOs cross boundaries (`dtos.py`); `user_uuid` is a plain UUIDField, never a
  FK (AGENTS.md §5).
- SPA: CSS Modules + design tokens (`styles.css:16-24`); 760px mobile
  breakpoint; accessibility-first (`role` + `aria-label` pinned by tests);
  `vi.spyOn(api, '<fn>')` not `vi.mock`; multipart helper sets only
  `X-CSRFToken`.

### Key Discoveries:

- **`process_image` writes 3 deliverables + the result dict onto the
  `ScoringJob` row** (`services.py:145-190`) and never deletes it. So "persist
  an accepted result" does NOT rescue rows from deletion — it adds an
  accept-state concept via a sibling model. The `ScoringJob` row stays the
  transient CV-pipeline record.
- **`_job_to_dto` rebuilds `ScoringResultDTO` from `job.result` JSON**
  (`services.py:282-339`) and is documented-fragile (raises `ValueError` on
  malformed). `marked_image_url` is resolved via `ScoringStorage()._storage.
  url(...)` at `:326-327`. Under Tigris (no public access), `.url(...)` returns
  a non-functional URL — Phase 4's presigned-URL policy fixes this.
- **The `MockDetector` rewrite breaks existing 5-hole assertions.**
  `test_mock_detector.py`, `test_services_q2.py`, `test_scoring_routes.py`, and
  the Playwright `scoring-flow.spec.ts` all assert on the fixed 5-hole pattern.
  Phase 3 migrates them to the new random-N shape (seeded for determinism).
- **Resource-oriented URI lesson is now in `lessons.md`.** The aggregation
  endpoint is `GET /v1/scores/aggregations` (plural noun, no verb), NOT
  `/v1/scoring/aggregate`. The existing `/v1/scoring/jobs` is already
  resource-named and stays. The new accept endpoint is `POST /v1/scoring/results`
  (the accepted-result resource), not `POST /v1/scoring/accept`.
- **`AGENTS.md §1` already corrected** (Railway/Tigris prod, persistent-disk
  language removed) — Phase 7 updates `infrastructure.md` to match.
- **The `.32ACP`/`.22LR`/`9x19mm`/`Slug` taxonomy bugs stay deferred.** The
  user's "modify little to none of vision code" directive + "70% good enough"
  mean the `caliber_taxonomy.py` split-brain fix is out of scope. `caliber_hint`
  remains free-text; the detector ignores it. (Deferred from S-02; not
  load-bearing for accept/persist/aggregate.)

## Desired End State

After S-03 lands:

1. **Persistence** — A user viewing a succeeded job at `/results/:jobId` sees
   the marked image + per-hole correction dropdowns + an **Accept** button and a
   **Reject** button. Accept (with the confirmed params form: caliber, distance,
   weapon_type, target_type) → `POST /v1/scoring/results` creates an
   `AcceptedResult` row snapshotting the corrected holes + confirmed params +
   computed score, then navigates to `/dashboard`. Reject → navigates away; no
   `AcceptedResult` is created (the `ScoringJob` row remains as a transient CV
   record). Accepted results are immutable after accept (PRD FR-010 Socrates:
   "editing saved results is v2").
2. **Aggregation** — The dashboard reads real data from
   `GET /v1/scores/aggregations`: hero stats (total shots = sum of accepted
   holes across all the user's `AcceptedResult` rows; last-session average =
   mean per-hole score across the most recent calendar day's accepted results;
   best result = max single-result average), a recent-results list, and a
   daily-average-past-month chart. The `mocks/dashboard.ts` fixtures are
   deleted; the three dashboard components call the real API.
3. **Prod detector on for real** — `VISION_DETECTOR=google` + `USE_S3=True`
   against Railway Tigris processes a real upload end-to-end: the q2 task body
   downloads the upload to a tempfile, runs `cv2.imread` + the Google detector,
   uploads the marked image back to Tigris, and surfaces a browser-fetchable
   presigned URL. The `NotImplementedError("S3 path ops land in S-03")` guards
   are gone.
4. **Dev mock** — `VISION_DETECTOR=mock` returns random N holes (default 10,
   scores 0–10, random `(x,y)` in the 1024×1024 frame, random confidence
   0.5–0.99), seeded for test determinism. The accept→persist→aggregate
   round-trip exercises varied data (multi-hole sums, max scores).
5. **Parameter confirmation** — The wizard collects caliber + distance +
   weapon_type + target_type (both ISSF types selectable). All four are
   persisted on `AcceptedResult`. The dead `distance_m` BFF mock field is
   replaced by a real forwarded param.
6. **Foundation docs** — `prd.md` §Success Criteria + NFR reflect "70% fidelity
   accepted as MVP bar (≥90% deferred)"; `roadmap.md` Open Question #1 resolved
   with the same decision; `infrastructure.md` reflects Railway/Tigris as the
   prod storage target (no longer "No managed object storage").

**Verification**: `make check` + `make be-test` + `make fe-test` pass; the
Playwright E2E drives dashboard → upload → wizard → waiting → results → Accept
→ dashboard-updated end-to-end; the prod detector path works against MinIO
(the Tigris-specific verification is a manual prod-deploy gate, not automated
in-sandbox).

## What We're NOT Doing

- **Closing the ≥90% fidelity wedge.** The user has decided 70% is good enough
  for MVP; the decision is *recorded* in Phase 1, not closed in code. No
  detector-accuracy work, no prompt engineering, no model swap.
- **Vision taxonomy bug fixes.** The `.32ACP`-missing / `.22LR`+`9x19mm`
  silent-DEFAULT / `Slug` split-brain bugs in `caliber_taxonomy.py` stay
  deferred. `caliber_hint` remains free-text; the detector ignores it.
- **First-class Session model.** "Session" is derived (same calendar day per
  user) — no new model, no migration, no UX for naming/starting a session.
- **Post-accept editing.** `AcceptedResult` is immutable after create (PRD
  FR-010 Socrates: "editing saved results is v2"; roadmap "Parked: Editing
  saved results"). Corrections happen pre-accept on the `/results/:jobId`
  screen and are snapshotted at accept.
- **Long-term image retention policy / privacy posture.** Roadmap Open Question
  #2 stays open. S-03 matches the existing posture (store uploads +
  deliverables). A retention lifecycle is a follow-up.
- **Per-user submission rate limit.** The `TODO(S-03)` at `scoring_routes.py:
  85-90` (429 on QUEUED+RUNNING ceiling) stays a TODO — single-user MVP.
- **CI/CD pipeline, multi-region, HA.** Out of MVP scope per
  `infrastructure.md`.
- **Adding Redux/Oval.** S-01 deferred it; S-03 uses refetch-on-navigate
  (dashboard refetches `getAggregations` on mount). If a store turns out to be
  genuinely needed, Phase 6 surfaces it — but the default is no store.

## Implementation Approach

**Phase ordering rationale.** Phases 1–2 are the persistence core (schema +
accept/reject services/routes) and must precede the aggregation and UI that
consume them. Phase 3 (MockDetector rewrite) is small and stands alone —
landing it before Phase 4 means the dev loop has varied data while the S3
refactor is in flight. Phase 4 (S3 refactor) is the load-bearing prod enabler
and lands before Phase 6 (SPA) so the prod path is real before the UI consumes
accepted results. Phase 5 (aggregation route) builds on Phase 2's
`AcceptedResult`. Phase 6 (SPA) wires the UI to Phases 2 + 5. Phase 7 folds
foundation-doc updates + E2E into one verification gate.

**Dev path (no Google API key):** `USE_S3=True` (MinIO via docker-compose, or
FS via host `make dev` with `USE_S3=False`) + `VISION_DETECTOR=mock` → the full
upload→poll→results→accept→dashboard round-trip is real; only the detector
output is mocked (random N holes).

**Prod path:** `USE_S3=True` (Tigris) + `VISION_DETECTOR=google` +
`GOOGLE_API_KEY` → real detection against a real upload, deliverables in
Tigris, presigned URLs for the SPA.

**Atomicity stays two-layered.** The new `accept_job` service wraps its own
`transaction.atomic()` (it writes `AcceptedResult` + reads `ScoringJob`); the
BFF's `POST /v1/scoring/results` also wraps (AGENTS.md §6.2). Both stay.

## Critical Implementation Details

- **The S3 refactor touches `process_image`, but only at the storage-call
  surface.** Today `process_image` calls `storage.absolute_path(job.input_path)`
  (`services.py:141`) expecting a `Path`, and `storage.deliverable_dir(...)`
  (`:146`) expecting a directory it can `.mkdir()`. Under S3 neither exists.
  Phase 4 changes the storage surface to be byte-oriented (`read_upload_bytes
  (stored_path) -> bytes` + `write_deliverable_bytes(job_id, name, bytes) ->
  stored_path`), and `process_image` writes the upload bytes to a
  `tempfile.NamedTemporaryFile` before handing the temp path to
  `PipelineRunner.run(...)`. The detector and pipeline logic are untouched —
  only the storage adapter + the call sites in `process_image` change
  (`:141` input load, `:146`/`:147` out_dir setup, `:159` `storage_root`
  computation, and the `:160-164` `_rel` helper that depends on it). This
  is the surgical scope of "modify little of the vision code."
- **`cv2.imread` cannot read S3.** This is the root reason the refactor is
  unavoidable under `USE_S3=True`. `PipelineRunner.run` takes a path and calls
  `cv2.imread` internally; the tempfile download is the bridge.
- **Presigned URLs for Tigris.** Under FS/MinIO, `default_storage.url(path)`
  returns a browser-fetchable URL. Tigris has no public access —
  `django-storages` with `AWS_QUERYSTRING_AUTH=True` (or explicit presigning)
  returns a time-limited signed URL. Phase 4 sets this in `settings.py` under
  the `USE_S3` block (default expire at django-storages' default, 3600s — well
  over the SPA's results-screen view time).
- **Acceptance is idempotent and race-free via a uniqueness constraint.**
  `AcceptedResult` declares `unique_together = ("source_job", "user_uuid")`.
  `POST /v1/scoring/results` for a job that already has an `AcceptedResult`
  returns the existing one (200) rather than creating a duplicate (201). The
  double-click / refresh-during-submit race is handled at the DB layer: the
  second concurrent insert raises `IntegrityError`, which the service catches
  and converts to a re-fetch + 200 (the canonical insert-or-return-existing
  idiom — the `IntegrityError` must be caught so it never surfaces as a 500).
  This is safer than a check-then-create sequence, which under SQLite's
  default isolation lets both transactions pass the existence check and both
  insert.
- **The `ScoringJob` row is NOT deleted on reject.** Reject is the absence of
  an `AcceptedResult`; the `ScoringJob` stays as a CV-pipeline audit record.
  This preserves the existing `get_job` ownership semantics and avoids a
  delete-path.
- **Derived session = calendar day in the server's timezone.** Aggregation
  groups `AcceptedResult` rows by `created_at__date` (SQLite `date()` function,
  server-local). "Last session" = the max date with ≥1 accepted result for the
  user. A shooter who accepts results morning and evening gets one session —
  acceptable for MVP per the user's decision.
- **`distance` + `weapon_type` land on `ScoringJob`, not just `AcceptedResult`.**
  They're inputs to the pipeline request (FR-009 "confirm params FOR a
  detection result"), collected in the wizard pre-upload. The BFF forwards them
  to `schedule_image_processing`, which persists them on `ScoringJob`.
  `AcceptedResult` snapshots them at accept. One migration adds both columns.

## Phase 1: Foundation — fidelity posture + distance/weapon_type columns

### Overview

Record the "70% good enough" fidelity decision in the PRD + roadmap, and add
the two missing input columns (`distance`, `weapon_type`) to `ScoringJob` with
a migration. Thread them through `schedule_image_processing`, the DTO, and the
BFF `ScoringJobIn` (replacing the dead `distance_m` mock field). Small,
individually-shippable backend change that unblocks Phases 2 and 6.

### Changes Required:

#### 1.1 `prd.md` — record the fidelity decision

**File**: `context/foundation/prd.md`

**Intent**: The PRD's §Success Criteria Primary (`:28`) and §NFR (`:111`) both
say "≥90% fidelity." The user has decided 70% is the MVP bar. Record this so
the foundation no longer contradicts the shipped reality.

**Contract**: Edit §Success Criteria Primary to note the MVP accepts ~70%
fidelity (measured 0.638–0.799 Jaccard in F-02) with the ≥90% bar deferred to a
post-MVP iteration. Edit §NFR the same way. Add a one-line note to FR-008's
Socrates block referencing the decision. Do NOT remove the ≥90% language
entirely — frame it as "deferred," not "abandoned," so a future iteration can
pick it up.

#### 1.2 `roadmap.md` — resolve Open Question #1

**File**: `context/foundation/roadmap.md`

**Intent**: Open Roadmap Question #1 (`:157`) asks "Is the CV ≥90% fidelity bar
achievable?" The user's answer is "70% is good enough for MVP." Resolve the
question.

**Contract**: Edit Open Question #1 to mark it resolved with the decision
("MVP ships at ~70% fidelity; ≥90% deferred"), dated 2026-07-28. Update the
S-02 Risk note (`:128`) and S-03 Risk note (`:141`) to reflect that the wedge
was confronted (the bar was lowered, not the gap closed).

#### 1.3 `ScoringJob` model — add `distance` + `weapon_type` columns

**File**: `src/domains/vision/models.py`

**Intent**: FR-009 requires confirming caliber, distance, AND weapon type.
`distance_m` is a dead BFF field today; weapon_type doesn't exist. Add both as
real columns on `ScoringJob` so they persist with the pipeline request and flow
through to `AcceptedResult` at accept.

**Contract**: Add two fields after `caliber_hint` (`:40`):
`distance = models.PositiveIntegerField(null=True, blank=True)` (meters; the
wizard's `DISTANCES_M` values are 7–500) and
`weapon_type = models.CharField(max_length=32, null=True, blank=True)`. Both
nullable because pre-existing rows (and the mock path) may not set them. Add a
docstring note that these are FR-009 confirmation params snapshotted onto
`AcceptedResult` at accept.

#### 1.4 Migration — `0004_scoringjob_distance_weapon_type`

**File**: `src/domains/vision/migrations/0004_scoringjob_distance_weapon_type.py` (new)

**Intent**: Persist the two new columns.

**Contract**: `dependencies = [("vision", "0003_schedule_stuck_job_reaper")]`.
Two `AddField` operations (`distance`, `weapon_type`), both `null=True,
blank=True` so existing rows survive. Run `uv run python src/manage.py
makemigrations vision` to generate, then verify the generated file matches the
model.

#### 1.5 `schedule_image_processing` — accept + forward `distance` + `weapon_type`

**File**: `src/domains/vision/services.py`

**Intent**: The service signature (`:70-76`) has no `distance` or `weapon_type`
params. Add them so the BFF can forward the wizard's selections.

**Contract**: Add `distance: Optional[int] = None` and
`weapon_type: Optional[str] = None` to the keyword-only signature. Persist them
on the `ScoringJob.objects.create(...)` call (`:83-89`). Update the docstring.
Do NOT pass them to the detector — `process_image` calls `PipelineRunner.run`
with `target_type` + `caliber_hint` only (the detector ignores distance/
weapon_type today); they're metadata for the accepted-result snapshot.

#### 1.6 `ScoringJobDTO` + `_job_to_dto` — surface the new fields

**File**: `src/domains/vision/dtos.py`, `src/domains/vision/services.py` (`_job_to_dto`)

**Intent**: The DTO (`dtos.py:37-60`) doesn't carry `distance` or `weapon_type`.
Add them so the SPA's `/results/:jobId` screen can pre-fill the accept form
with the wizard's selections.

**Contract**: Add `distance: Optional[int] = None` and
`weapon_type: Optional[str] = None` to `ScoringJobDTO` (after `caliber_hint`).
In `_job_to_dto` (`services.py:329-339`), populate both from the `job` row.

#### 1.7 `ScoringJobIn` + `create_scoring_job` — replace the dead `distance_m`

**File**: `src/bff/routers/scoring_routes.py`

**Intent**: `ScoringJobIn.distance_m` (`:66`) is parsed and dropped. Replace
with real `distance` + `weapon_type` fields forwarded to
`schedule_image_processing`. Also add `target_type` selection is already
present (the `Literal` at `:64` accepts both ISSF types) — no change needed
there, but the wizard (Phase 6) will stop hardcoding it.

**Contract**: Replace the `distance_m: int | None = None` line with
`distance: int | None = None` and add `weapon_type: str | None = None`. In
`create_scoring_job` (`:99-104`), forward both to `schedule_image_processing`.
Update the module docstring (`:22-24`) — remove the "dropped on the floor"
note. Optionally add a `weapon_type` `Literal` for validation (ISSF-appropriate
values: `air_pistol`, `sport_pistol`, `free_pistol`, `revolver` — confirm the
list in Phase 6 when the wizard's `WEAPON_TYPES` is defined; for Phase 1 leave
it as free-text `str | None` to avoid a premature constraint).

#### 1.8 Regression tests

**File**: `src/domains/vision/tests/test_services_q2.py` (extend),
`tests/system/test_scoring_routes.py` (extend)

**Intent**: Pin that `distance` + `weapon_type` flow end-to-end.

**Contract**: In `test_services_q2.py`, add a test that `schedule_image_
processing(..., distance=25, weapon_type="air_pistol")` creates a `ScoringJob`
with both fields set. In `test_scoring_routes.py`, add a test that `POST
/v1/scoring/jobs` with `distance=25` + `weapon_type="air_pistol"` form fields
→ 201, and the created `ScoringJob` row carries both (query the ORM in the
test). Migrate any existing test that asserted the old `distance_m` field name.

### Success Criteria:

#### Automated Verification:

- `uv run python src/manage.py makemigrations --check --dry-run vision`
  reports no pending migration (the 0004 file matches the model)
- `uv run python src/manage.py migrate` applies cleanly
- `uv run pytest src/domains/vision/tests/test_services_q2.py` passes
  (including the new distance/weapon_type test)
- `uv run pytest tests/system/test_scoring_routes.py` passes (including the
  new forwarding test)
- `make check` passes (import-linter independence intact)

#### Manual Verification:

- The PRD + roadmap edits read coherently — the ≥90% bar is framed as
  "deferred," not "abandoned"
- With `VISION_DETECTOR=mock make dev`, a POST carrying `distance` +
  `weapon_type` form fields persists both on the `ScoringJob` row (verify via
  `/admin/vision/scoringjob/`)

**Implementation Note**: After completing this phase and all automated
verification passes, pause here for manual confirmation from the human that the
manual testing was successful before proceeding to the next phase. Phase blocks
use plain bullets — the corresponding `- [ ]` checkboxes for these items live
in the `## Progress` section at the bottom of the plan.

---

## Phase 2: AcceptedResult model + accept/reject BFF routes

### Overview

The greenfield core. New `AcceptedResult` model (1:1 to `ScoringJob` by UUID,
holding confirmed params + corrected-hole snapshot + computed score). New
vision service `accept_job(...)` (creates `AcceptedResult` from a succeeded
job + confirmed payload). New BFF route `POST /v1/scoring/results` (accept).
Reject is the absence of a POST — no route needed (the SPA just navigates
away). Idempotent on re-POST.

### Changes Required:

#### 2.1 `AcceptedResult` model

**File**: `src/domains/vision/models.py` (extend)

**Intent**: The persisted, user-confirmed score. Sibling to `ScoringJob` (not
a flag on it) so the CV-pipeline lifecycle and the user-accept lifecycle stay
clean. Aggregation queries this.

**Contract**: Add a new `AcceptedResult` class to the existing
`src/domains/vision/models.py` (alongside `ScoringJob`). This matches the
repo's established convention — every domain (vision, identity, core) uses a
single `models.py`, and Django auto-discovers models in the app's `models`
module, so no import-to-register dance is needed and `makemigrations vision`
picks it up automatically. (The "One class per file" lesson in `lessons.md`
targets code-gen grab-bag modules and explicitly exempts contract modules like
`ports.py`/`dtos.py`; it is not about Django ORM models, and applying it here
would both break from every existing domain and create a model-discovery
footgun.) Declare `class Meta: app_label = "vision"` and
`db_table = "vision_acceptedresult"` to match `ScoringJob`'s pattern. Fields:
- `id` UUID PK
- `user_uuid` UUIDField `db_index=True` (AGENTS.md §5 — plain UUID, no FK)
- `source_job` UUIDField (the `ScoringJob.id` it was accepted from — plain UUID,
  no FK; allows the CV row to be reclaimed independently if ever needed)
- `target_type` CharField(32)
- `caliber_hint` CharField(64) nullable
- `distance` PositiveIntegerField nullable
- `weapon_type` CharField(32) nullable
- `holes` JSONField (the corrected-hole snapshot — list of `{x, y, score,
  confidence, caliber}`)
- `score_average` FloatField (mean of the holes' scores — the "result" for
  hero-stats + chart aggregation)
- `created_at` auto_now_add
- `Meta.db_table = "vision_acceptedresult"`
- `Meta.unique_together = ("source_job", "user_uuid")` — DB-enforced
  idempotency for the accept path (see Critical Implementation Details); a
  second concurrent accept for the same job raises `IntegrityError`, caught
  in the service and converted to the 200 idempotent re-fetch path.

Add `default=uuid.uuid4` to the `id` field. The `holes` JSONField stores the
corrected snapshot (detector output + user corrections applied pre-accept).

#### 2.2 Migration — `0005_acceptedresult`

**File**: `src/domains/vision/migrations/0005_acceptedresult.py` (new)

**Intent**: Persist the new model.

**Contract**: `dependencies = [("vision", "0004_scoringjob_distance_weapon_
type")]`. `CreateModel` for `AcceptedResult` (including its
`unique_together = ("source_job", "user_uuid")` constraint — the generated
migration will emit it as a `AlterUniqueTogether` op in the same file; verify
after `makemigrations`). Run `makemigrations vision` to generate.

#### 2.3 DTOs — `AcceptedResultDTO` + `AcceptResultIn`

**File**: `src/domains/vision/dtos.py` (extend)

**Intent**: The wire contract for the accept payload + response.

**Contract**: Add `AcceptedResultDTO` (Pydantic): `result_id: UUID`,
`source_job: UUID`, `target_type`, `caliber_hint: Optional[str]`,
`distance: Optional[int]`, `weapon_type: Optional[str]`, `holes:
List[DetectedHoleDTO]`, `score_average: float`, `created_at: Optional[str]`.
Add `AcceptResultIn` (the request body): `job_id: UUID` (the `ScoringJob` the
SPA is accepting — the route `POST /scoring/results` is resource-named with no
path param, so the job identifier rides in the body), `target_type`,
`caliber_hint: Optional[str]`, `distance: Optional[int]`,
`weapon_type: Optional[str]`, `holes: List[DetectedHoleDTO]` (the corrected
snapshot — the SPA sends the detector's holes with any corrections applied).

#### 2.4 `accept_job` service

**File**: `src/domains/vision/services.py` (extend)

**Intent**: The vision-domain surface the BFF calls. Reads the `ScoringJob`,
enforces ownership, creates `AcceptedResult` from the confirmed payload.

**Contract**: New function `accept_job(*, job_id: str | UUID, user_uuid: UUID,
target_type: TargetType, caliber_hint: Optional[str], distance: Optional[int],
weapon_type: Optional[str], holes: List[DetectedHoleDTO]) -> AcceptedResultDTO`.
Inside `transaction.atomic()`: fetch the `ScoringJob` (raise `PermissionError`
on missing/mismatch, same as `get_job`); **enforce state — raise a typed
`StateError` (a small domain exception defined alongside the service, mirroring
the existing `PermissionError` convention) if `job.status != SUCCEEDED`; a
queued/running/failed job cannot be accepted**; compute
`score_average = mean(h.score for h in holes)`; attempt to create the
`AcceptedResult` — on `IntegrityError` (a concurrent accept for the same job
won the `unique_together` race), re-fetch the existing `AcceptedResult` and
return it (the 200 idempotent path). Return the DTO with the row that won
(whether it's this call's or the re-fetched one). Do NOT delete the
`ScoringJob` — it stays as the CV audit record. Do NOT use a check-then-create
sequence: under SQLite's default isolation two concurrent transactions both
pass the check and both insert, so the `unique_together` constraint + caught
`IntegrityError` is the arbiter, not a pre-read.

#### 2.5 BFF route — `POST /v1/scoring/results`

**File**: `src/bff/routers/scoring_routes.py` (extend)

**Intent**: The accept endpoint. Resource-named (`/scoring/results`, the
accepted-result resource) per the new API-design lesson — NOT `/scoring/accept`.

**Contract**: New route `@router.post("/scoring/results", auth=session_auth,
response={201: AcceptedResultDTO, 200: AcceptedResultDTO})`.
`@transaction.atomic`. Body is `AcceptResultIn` (JSON, not multipart). Resolve
`user_uuid` via `get_user_context` (same `try/except DoesNotExist → 401` wrap
as the existing routes). The `job_id` comes from the body (the `AcceptResultIn`
field added in 2.3 — the SPA knows which job it's accepting from the route's
`/results/:jobId` param and sends it as `job_id` in the POST body). Call
`accept_job(job_id=payload.job_id, user_uuid=..., target_type=payload.target_type,
caliber_hint=payload.caliber_hint, distance=payload.distance,
weapon_type=payload.weapon_type, holes=payload.holes)`. Return the
`AcceptedResultDTO`; the response status is 201 on first accept, 200 on the
idempotent re-POST (use django-ninja's `response={201: ..., 200: ...}` and
return a tuple `(status, dto)` — see django-ninja docs for multiple response
codes). **Error mapping (route-owned, mirroring the existing
`PermissionError → HttpError(404)` pattern)**: catch `PermissionError`
(missing/ownership mismatch) → `HttpError(404)`; catch `StateError`
(non-succeeded job) → `HttpError(409, "Job not succeeded")`. The status guard
itself lives in the service (2.4) so the domain owns the rule; the route only
translates the exception to HTTP, exactly as it does for `PermissionError`.

#### 2.6 System tests — accept contract

**File**: `tests/system/test_scoring_routes.py` (extend)

**Intent**: Pin the accept route's contract.

**Contract**: Module marker unchanged. The `AcceptResultIn` body in every test
MUST include `job_id` (the field added in 2.3). Test cases:
- `POST /v1/scoring/results` for a succeeded job owned by the user, with a
  valid `AcceptResultIn` body (including `job_id`) → 201, returns
  `AcceptedResultDTO` with the snapshot holes + `score_average` = mean of the
  sent holes
- `POST` for the same job again → 200, returns the SAME `result_id` (idempotent)
- **Race**: two concurrent POSTs for the same job (e.g. via `threading`) both
  return 200, AND exactly one `AcceptedResult` row exists afterwards (the
  `unique_together` constraint + caught `IntegrityError` prevents a duplicate,
  not the check-then-create sequence). This is the load-bearing regression
  test for the F1 fix.
- `POST` for a job owned by another user → 404 (ownership via `PermissionError`)
- `POST` for a nonexistent job → 404
- `POST` anon → 401
- `POST` for a job still in `queued`/`running`/`failed` → 409 Conflict (the
  service raises `StateError` when `job.status != SUCCEEDED`; the route maps
  it to `HttpError(409, "Job not succeeded")` — see 2.4 / 2.5)
- `POST` with an empty `holes` list → 422 (Pydantic validation —
  `List[DetectedHoleDTO]` with `min_items=1`, or an explicit check in
  `accept_job`)

Seed a succeeded job via `schedule_image_processing` + `process_image` (or
directly construct a `ScoringJob(status=SUCCEEDED, result={...})` for
isolation).

### Success Criteria:

#### Automated Verification:

- `uv run python src/manage.py makemigrations --check --dry-run vision` reports
  no pending migration
- `uv run python src/manage.py migrate` applies cleanly
- `uv run pytest tests/system/test_scoring_routes.py` passes (including the new
  accept-contract tests)
- `make check` passes
- `make be-test` passes (no regressions)

#### Manual Verification:

- With `VISION_DETECTOR=mock make dev`, after a job succeeds, `curl -X POST
  /v1/scoring/results` with the job_id + a holes payload → 201; a second POST
  → 200 with the same `result_id`
- The `AcceptedResult` row is visible in `/admin/vision/acceptedresult/` (add
  it to the admin if not auto-registered — check
  `src/domains/vision/admin.py`)

**Implementation Note**: After completing this phase and all automated
verification passes, pause for manual confirmation from the human that the
manual testing was successful before proceeding to the next phase.

---

## Phase 3: MockDetector rewrite (random N holes)

### Overview

Replace the fixed 5-hole pattern with random N holes (default 10, scores 0–10,
random `(x,y)` in the 1024×1024 frame, random confidence 0.5–0.99), seeded for
test determinism. Honors the user's dev-mock directive and gives the
accept→persist→aggregate round-trip varied data. Migrate the existing 5-hole
assertions.

### Changes Required:

#### 3.1 `MockDetector.detect` — random N holes, seedable

**File**: `src/domains/vision/detectors/mock_detector.py`

**Intent**: The fixed 5-hole pattern (`:29-35`) doesn't exercise aggregation
bounds (multi-hole sums, max scores). Rewrite to emit random N holes. The
detector must stay deterministic in tests so assertions are stable.

**Contract**: Replace `_MOCK_HOLES` and the `detect` body. Read the seed from
an env var (`MOCK_DETECTOR_SEED`) — if set, construct `random.Random(seed)`;
otherwise use the module-level `random` (unseeded, varies per run). Read the
hole count from `MOCK_DETECTOR_HOLE_COUNT` (default 10). For each hole: random
`x ∈ [50, 974]`, `y ∈ [50, 974]` (inset so holes land on the target face, not
the edge), random `score ∈ [0, 10]`, random `confidence ∈ [0.5, 0.99]`. The
`notes` field reflects "Mock detector: random N-hole pattern (seed=...,
count=...)." Keep the `name = "mock"` property and the `DetectionResult` return
shape. The seed reading happens in `detect` (not `__init__`) so the
`DetectorFactory.build("mock")` call stays parameterless — the env vars are
read at detect-time.

#### 3.2 Migrate the 5-hole-asserting tests

**Files**: `src/domains/vision/tests/test_mock_detector.py`,
`src/domains/vision/tests/test_services_q2.py`,
`tests/system/test_scoring_routes.py`,
`src/frontend/tests-acceptance/scoring-flow.spec.ts`

**Intent**: The existing tests assert on the fixed 5-hole pattern. Phase 3.1
breaks them. Migrate to the new shape.

**Contract**: In each test, set `MOCK_DETECTOR_SEED=固定value` +
`MOCK_DETECTOR_HOLE_COUNT=N` via `monkeypatch.setenv` (pytest) or
`extra_env` (Playwright `global-setup.ts`) so the assertions are deterministic.
Replace `len(holes) == 5` with `len(holes) == N` (the configured count).
Replace specific-hole assertions (e.g. "score 10 at (512,512)") with
shape-level assertions (each hole has `{x, y, score, confidence}`, scores in
`[0,10]`, x/y in `[0,1024]`). For the Playwright `scoring-flow.spec.ts`, the
"5 per-hole correction dropdowns" assertion (`8.5`/`8.6`) becomes "N dropdowns"
— set the count in `global-setup.ts` and assert on it. The seed makes the
visual output deterministic for screenshot-based debugging.

### Success Criteria:

#### Automated Verification:

- `uv run pytest src/domains/vision/tests/test_mock_detector.py` passes (new
  random-N shape)
- `uv run pytest src/domains/vision/tests/test_services_q2.py` passes (migrated)
- `uv run pytest tests/system/test_scoring_routes.py` passes (migrated — the
  5-hole assertion becomes an N-hole assertion with `MOCK_DETECTOR_HOLE_COUNT`
  pinned)
- `cd src/frontend && npx playwright test` passes (migrated scoring-flow.spec)
- `make check` passes

#### Manual Verification:

- With `VISION_DETECTOR=mock make dev`, two consecutive jobs return DIFFERENT
  hole patterns (unseeded dev path varies); with `MOCK_DETECTOR_SEED=42
  make dev`, two consecutive jobs return the SAME pattern (seeded)

**Implementation Note**: After completing this phase and all automated
verification passes, pause for manual confirmation from the human that the
manual testing was successful before proceeding to the next phase.

---

## Phase 4: S3-compatible storage refactor (the prod enabler)

### Overview

Replace the `NotImplementedError` guards in `ScoringStorage`'s path methods
with real S3 implementations: download the upload to a tempfile before
`cv2.imread`, replace the filesystem containment check with a prefix-based S3
containment check, upload deliverables back to S3 after the pipeline runs.
Update `process_image` to use the new byte-oriented surface. Set the Tigris
presigned-URL policy in `settings.py`. **Scoped to the storage adapter + the
3 call sites in `process_image` — no detector or pipeline logic changes.** This
is the surgical scope of "modify little of the vision code."

### Changes Required:

#### 4.1 `ScoringStorage` — byte-oriented surface + S3 implementations

**File**: `src/domains/vision/pipeline/storage.py`

**Intent**: Under `USE_S3=True`, `cv2.imread` cannot read an S3 key and S3 has
no directories. The path-shaped methods (`_safe_join`, `absolute_path`,
`read_upload`, `write_deliverable`, `deliverable_dir`) raise today. Replace
with a byte-oriented surface that works under both backends, and implement the
S3 branches.

**Contract**: Add two new methods that work under both FS and S3:
- `read_upload_bytes(stored_path: str) -> bytes` — under FS, `self._safe_join(
  stored_path).read_bytes()` (existing `read_upload` body); under S3,
  `self._storage.open(stored_path, "rb").read()`. This is what
  `process_image` calls to get the upload bytes before writing them to a
  tempfile.
- `write_deliverable_bytes(job_id: UUID, name: str, data: bytes) -> str` —
  under FS, the existing `write_deliverable` body (mkdir + write + relative
  path); under S3, `self._storage.save(f"jobs/{job_id}/{name}",
  ContentFile(data))` and return `f"jobs/{job_id}/{name}"` (the stored key).

Replace the `NotImplementedError` guard in `_safe_join` (`:79-80`) and the
guard block in `__init__` (`:52-59`) — under S3, `_root` stays `None` and
`_safe_join` is no longer called (the new byte-oriented methods don't use it).
Keep `_safe_join` for the FS path (the explicit-`location` test/CLI branch
still uses it). The old path-shaped methods (`absolute_path`, `read_upload`,
`write_deliverable`, `deliverable_dir`) can stay as thin FS-only wrappers that
raise `NotImplementedError` under S3 — they're no longer called by
`process_image` after 4.2, but keeping them avoids breaking any external CLI
callers. Document this in the docstring.

The prefix-based containment check under S3 lives implicitly in
`self._storage.save(name, ...)` (django-storages sanitizes the key) — no
explicit `_safe_join` equivalent is needed because the new methods construct
keys from controlled components (`f"jobs/{job_id}/{name}"` where `name` is one
of the 3 fixed deliverable filenames).

#### 4.2 `process_image` — tempfile dance for `cv2.imread`

**File**: `src/domains/vision/services.py` (`process_image`, `:130-192`)

**Intent**: `process_image` calls `storage.absolute_path(job.input_path)`
(`:141`) expecting a `Path`, and `storage.deliverable_dir(...).mkdir()`
(`:146`) expecting a directory. Both are FS-only. Switch to the byte-oriented
surface + a tempfile.

**Contract**: Replace the `input_abspath = storage.absolute_path(job.input_
path)` line (`:141`) with: read bytes via `storage.read_upload_bytes(job.input_path)`,
write them to a `tempfile.NamedTemporaryFile(suffix=Path(job.input_path).
suffix, delete=False)`, and pass the temp path to `PipelineRunner.run(...)`.
After the run, unlink the temp file (in a `finally`). Replace the
`out_dir.mkdir(...)` (`:146-147`) + `runner.run(..., out_dir=out_dir)` block:
have `PipelineRunner.run` write deliverables to a temp directory
(`tempfile.TemporaryDirectory()`), then upload each deliverable back via
`storage.write_deliverable_bytes(job_uuid, name, data)` where `data` is the
deliverable's bytes (`(tmp_dir / f"{stem}_llm_input.png").read_bytes()` etc.).
**The `storage_root` computation (`:159`,
`storage_root = Path(storage.absolute_path(".")).resolve()`) is removed
entirely** — it is FS-only (it calls `absolute_path`, which raises under S3
per 4.1) and only exists to feed `_rel`. The `_rel(p)` helper (`:160-164`) is
**deleted, not rewritten**: `write_deliverable_bytes` returns the stored key
directly, so the job's `llm_input_path`/`marked_image_path`/`result_json_path`
are set from those return values, not from `_rel(out_dir / filename)`. So the
call sites that change are `:141`, `:146`/`:147`, AND `:159` (the `_rel`
helper at `:160-164` goes away with it). The detector + `PipelineRunner`
internals are untouched.

**Snippet** (the load-bearing transformation, non-obvious because the temp
file's lifecycle must wrap the pipeline run):

```python
import tempfile
# ...
upload_bytes = storage.read_upload_bytes(job.input_path)
with tempfile.NamedTemporaryFile(suffix=Path(job.input_path).suffix, delete=False) as tf:
    tf.write(upload_bytes)
    input_abspath = Path(tf.name)
try:
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)
        stem = Path(job.input_path).stem
        result_dict = runner.run(
            input_abspath, target_type=job.target_type,
            caliber_hint=job.caliber_hint, out_dir=out_dir,
        )
        llm_key = storage.write_deliverable_bytes(job_uuid, f"{stem}_llm_input.png",
                                                  (out_dir / f"{stem}_llm_input.png").read_bytes())
        marked_key = storage.write_deliverable_bytes(job_uuid, f"{stem}_marked.png",
                                                     (out_dir / f"{stem}_marked.png").read_bytes())
        result_json_key = storage.write_deliverable_bytes(job_uuid, f"{stem}_result.json",
                                                          (out_dir / f"{stem}_result.json").read_bytes())
        # The stored keys replace the old _rel(out_dir / filename) values —
        # no storage_root / _rel helper needed.
        llm_path, marked_path, result_json_path = llm_key, marked_key, result_json_key
finally:
    input_abspath.unlink(missing_ok=True)
```

(The existing `from src.domains.vision.pipeline.pipeline_runner import
json_default` import + the `_sanitize_nan_inf`/`json.dumps` block for
`job.result` stay unchanged — that's the DB-row serialization, not the
deliverable-write.)

#### 4.3 `settings.py` — Tigris presigned-URL policy

**File**: `src/target_o_meter/settings.py`

**Intent**: Under FS/MinIO, `default_storage.url(path)` returns a
browser-fetchable URL. Tigris has no public access — the SPA's
`<img src={marked_image_url}>` would 403. django-storages' presigned-URL mode
fixes this.

**Contract**: In the `if USE_S3:` block (`:359-366`), add
`AWS_QUERYSTRING_AUTH = True` (django-storages returns time-limited signed
URLs from `.url(...)`) and `AWS_QUERYSTRING_EXPIRE = 3600` (1 hour — well over
the results-screen view time). For MinIO (where `AWS_S3_ENDPOINT_URL` is set),
these settings are harmless (MinIO honors presigned URLs too) — so the same
settings work for both the docker-compose MinIO path and prod Tigris.
`_job_to_dto`'s `storage._storage.url(job.marked_image_path)` call
(`services.py:327`) now returns a signed URL under both paths.

#### 4.4 Storage + process_image S3-path tests

**File**: `src/domains/vision/tests/test_storage_swap.py` (extend),
`src/domains/vision/tests/test_services_q2.py` (extend)

**Intent**: Pin that the S3 path no longer raises and that the byte-oriented
surface round-trips.

**Contract**: In `test_storage_swap.py`, add tests with `USE_S3=True` +
MinIO-style env monkeypatched (the S3 backend constructed against a mock —
use `moto` or stub `self._storage` to assert the new methods are called).
`read_upload_bytes` + `write_deliverable_bytes` round-trip bytes; the old
path methods still raise under S3 (for the CLI path). In `test_services_q2.py`,
add a test that `process_image` under `USE_S3=True` (mocked storage) completes
without `NotImplementedError` — patch `ScoringStorage` to a fake that records
the `read_upload_bytes` + `write_deliverable_bytes` calls. This is the
load-bearing regression test for Phase 4.2.

### Success Criteria:

#### Automated Verification:

- `uv run pytest src/domains/vision/tests/test_storage_swap.py` passes (new
  S3-path tests)
- `uv run pytest src/domains/vision/tests/test_services_q2.py` passes
  (process_image under mocked S3 completes)
- `make check` passes
- `make be-test` passes (no regressions in the FS path — the existing
  `USE_S3=False` tests still pass because the new methods fall back to the FS
  implementations)

#### Manual Verification:

- With `make dev-container` (MinIO + `USE_S3=True` + `VISION_DETECTOR=mock`),
  a POST → poll → succeeded round-trip completes WITHOUT the
  `NotImplementedError: S3 path ops land in S-03` error that S-02's Phase 5.8
  documented; the marked image is browser-fetchable from the MinIO URL
- The `marked_image_url` returned by `GET /v1/scoring/jobs/{id}` is a signed
  URL (verify via `/admin/` or curl)

**Implementation Note**: After completing this phase and all automated
verification passes, pause for manual confirmation from the human that the S3
path works against MinIO before proceeding. The Tigris-specific verification
is a manual prod-deploy gate (Phase 7), not automatable in-sandbox.

---

## Phase 5: Aggregation BFF route (`GET /v1/scores/aggregations`)

### Overview

New BFF route returning hero stats + recent results + daily averages, computed
on read from `AcceptedResult` (no materialized stats table). Last session = the
most recent calendar day with an accepted result per the user's "derived
session" decision. Resource-named per the API-design lesson.

### Changes Required:

#### 5.1 Aggregation DTOs

**File**: `src/domains/vision/dtos.py` (extend)

**Intent**: The wire contract for the dashboard.

**Contract**: Add `HeroStatsDTO` (`total_shots: int`,
`last_session_average: Optional[float]`, `best_result: Optional[float]`).
Add `ResultSummaryDTO` (`result_id: UUID`, `created_at: str`, `score_average:
float`, `hole_count: int`, `target_type: str`). Add `DailyAverageDTO` (`date:
str`, `average: float`). Add `AggregationDTO` (`hero: HeroStatsDTO`, `recent:
List[ResultSummaryDTO]`, `daily_averages: List[DailyAverageDTO]`).

**These DTOs are the new canonical shapes — the dashboard swap is a shape AND
scale change, not just a data-source change.** The S-02 fixture
`mocks/dashboard.ts` `ResultSummary` is `{ jobId, date, score /* 0-100 */,
targetCount }`; the DTO is `{ result_id, created_at, score_average /* 0-10 */,
hole_count, target_type }` — different field names, different identity
(`jobId` → `result_id`), and a different scale (`score` 0-100 in the mock vs.
`score_average` 0-10 here). The 0-10 scale is intentional: it matches the
PRD's 0-10 + X scoring domain (AGENTS.md §2), so the mock's 0-100 was always
arbitrary. **Phase 6.6 must rewrite the three dashboard child components**
(field names + scale handling) to consume these DTOs — it is NOT a one-line
import change per consumer despite the S-02 fixture's aspirational comment.

#### 5.2 `aggregate_for_user` service

**File**: `src/domains/vision/services.py` (extend)

**Intent**: Compute the three aggregations from `AcceptedResult` rows for a
given `user_uuid`. Pure-query logic; no HTTP.

**Contract**: New function `aggregate_for_user(*, user_uuid: UUID, recent_
limit: int = 10, days: int = 30) -> AggregationDTO`. Three computations:
- `total_shots` = **the sum of hole counts across the user's accepted
  results** (NOT the count of `AcceptedResult` rows). The PRD US-01 says
  "total shots," which most naturally reads as the count of holes; a shooter
  who accepts one result with 10 holes has 10 shots, not 1. Implement via
  `AcceptedResult.objects.filter(user_uuid=...).annotate(c=KeyLength("holes"))`
  aggregation or, more simply, sum the Python-side lengths in the `recent` +
  date-group query results (the dataset is small at MVP scale). Pinned here so
  the Phase 5.4 assertions and the `HeroStats.total_shots` UI are unambiguous.
- `last_session_average` = the max `created_at__date` with ≥1 accepted result;
  the mean `score_average` across that date's accepted results. `None` if the
  user has no accepted results.
- `best_result` = `max(score_average)` across the user's accepted results.
  `None` if none.
- `recent` = the `recent_limit` most recent `AcceptedResult` rows, mapped to
  `ResultSummaryDTO`.
- `daily_averages` = for each of the last `days` days, the mean `score_average`
  of accepted results on that date (0-entry days either omitted or included as
  `average=None` — match the chart's expectation; the mocked fixture omits
  zero days, so omit them).

Use Django's `annotate`/`values`/`Count`/`Avg` + `TruncDate` for the
date-grouped queries (server-side; SQLite handles these). Wrap in a single
query where possible to keep the dashboard load light.

#### 5.3 BFF route — `GET /v1/scores/aggregations`

**File**: `src/bff/routers/scoring_routes.py` (extend)

**Intent**: The dashboard's single aggregation endpoint. Resource-named
(`/scores/aggregations`, plural noun) per the API-design lesson — NOT
`/scoring/aggregate`. Lives under `/v1/scores/` to distinguish the aggregated-
result resource from the `/v1/scoring/jobs` pipeline resource.

**Contract**: New route `@router.get("/scores/aggregations", auth=session_auth,
response={200: AggregationDTO})`. Resolve `user_uuid` via `get_user_context`
(same `try/except DoesNotExist → 401` wrap). Call
`aggregate_for_user(user_uuid=user_dto.user_uuid)`. Return the DTO. An owner
sees their own aggregations (the owner is also a shooter per PRD §Access
Control) — no special owner path.

#### 5.4 System tests — aggregation contract

**File**: `tests/system/test_aggregation_routes.py` (new)

**Intent**: Pin the aggregation route's contract + the derived-session logic.

**Contract**: Module marker `pytestmark = [pytest.mark.django_db,
pytest.mark.dev]`. Seed `AcceptedResult` rows via the test_utils (extend
`src/domains/vision/test_utils.py` with an `make_accepted_result` seeder —
today it's an empty stub). Test cases:
- `GET /v1/scores/aggregations` anon → 401
- `GET` for a user with no accepted results → 200, hero stats all `None`/`0`,
  empty `recent`, empty `daily_averages`
- `GET` for a user with 3 accepted results on 3 different days → 200, hero
  stats computed correctly (`total_shots` = sum of holes, `best_result` = max
  `score_average`, `last_session_average` = the most-recent-day's mean),
  `recent` has 3 entries, `daily_averages` has 3 entries
- `GET` for a user with 2 accepted results on the SAME day → `last_session_
  average` = the mean across both (derived-session = calendar day)
- Cross-user isolation: user A's results don't appear in user B's aggregation

### Success Criteria:

#### Automated Verification:

- `uv run pytest tests/system/test_aggregation_routes.py` passes (all cases)
- `make check` passes
- `make be-test` passes (no regressions)

#### Manual Verification:

- After accepting a few results (via the Phase 2 route + the Phase 6 UI),
  `curl /v1/scores/aggregations` returns the computed hero stats + recent +
  daily chart data
- The derived-session logic is correct: accept two results on the same day,
  verify `last_session_average` is their mean

**Implementation Note**: After completing this phase and all automated
verification passes, pause for manual confirmation from the human that the
manual testing was successful before proceeding to the next phase.

---

## Phase 6: SPA — params + Accept/Reject on /results + dashboard swap

### Overview

Extend `CaliberDistanceStep` to collect weapon_type + target_type; add Accept/
Reject buttons + the accept form to `/results/:jobId`; swap the three dashboard
components from `mocks/dashboard.ts` to real API calls; add the `acceptResult`
+ `getAggregations` helpers to `api.ts`. The dashboard refetches aggregations
on mount (no Redux/Oval — refetch-on-navigate suffices).

### Changes Required:

#### 6.1 `taxonomy.ts` — add `WEAPON_TYPES` + `TARGET_TYPES`

**File**: `src/frontend/src/taxonomy.ts`

**Intent**: The wizard needs weapon_type + target_type lists. Define them
alongside the existing `CALIBERS` + `DISTANCES_M`.

**Contract**: Add `WEAPON_TYPES = ['air_pistol', 'sport_pistol', 'free_pistol',
'revolver'] as const` (ISSF-appropriate; confirm against the BFF's accepted
values — if Phase 1.7 left `weapon_type` as free-text, these are UI-only
suggestions). Add `TARGET_TYPES = ['air_pistol', 'precision_pistol'] as const`
(the two ISSF types vision supports per `vision/ports.py:21`). Add
`type WeaponType = (typeof WEAPON_TYPES)[number]` and `type TargetType =
(typeof TARGET_TYPES)[number]`. Update the module docstring.

#### 6.2 `CaliberDistanceStep` — collect all four params

**File**: `src/frontend/src/components/CaliberDistanceStep.tsx`

**Intent**: Today it collects `{caliber, distance_m}` only. Extend to
`{caliber, distance_m, weapon_type, target_type}` so FR-009 is satisfied.

**Contract**: Extend the `CaliberDistanceSelection` interface to include
`weapon_type: string` + `target_type: string`. Add two `<select>` elements
(weapon_type from `WEAPON_TYPES`, target_type from `TARGET_TYPES`) with
accessible `<label>`s. The default `target_type` is `'air_pistol'` (preserving
S-02 behavior) but now user-selectable. The default `weapon_type` is
`WEAPON_TYPES[0]`. On "Next," pass all four via `onNext`.

#### 6.3 `Capture.tsx` + `Upload.tsx` — thread the new params

**Files**: `src/frontend/src/components/Capture.tsx`,
`src/frontend/src/components/Upload.tsx`

**Intent**: Both hardcode `target_type: 'air_pistol'` (`:31`, `:28`) with
`TODO(S-03)` markers. Thread the wizard's selections through to
`createScoringJob`.

**Contract**: Update the `createScoringJob(...)` calls to pass
`selection.target_type` instead of the hardcoded `'air_pistol'`, and add
`selection.weapon_type`. Remove the `TODO(S-03)` block comments. Update
`createScoringJob`'s signature in `api.ts` (6.5) to accept `weapon_type`.

#### 6.4 `api.ts` — `acceptResult` + `getAggregations` + param threading

**File**: `src/frontend/src/api.ts`

**Intent**: New helpers for the accept route + the aggregation route, and
signature updates for the new params.

**Contract**: Update `createScoringJob` to accept `weapon_type?: string` and
append it to the `FormData`. Add the accept + aggregation types and helpers:

```ts
export interface AcceptedHole { x: number; y: number; score: number; confidence: number; caliber?: string | null; }
export interface AcceptedResult {
  result_id: string; source_job: string; target_type: string;
  caliber_hint?: string | null; distance?: number | null; weapon_type?: string | null;
  holes: AcceptedHole[]; score_average: number; created_at?: string | null;
}
export interface HeroStats { total_shots: number; last_session_average: number | null; best_result: number | null; }
export interface ResultSummary { result_id: string; created_at: string; score_average: number; hole_count: number; target_type: string; }
export interface DailyAverage { date: string; average: number; }
export interface Aggregations { hero: HeroStats; recent: ResultSummary[]; daily_averages: DailyAverage[]; }

export async function acceptResult(
  jobId: string, payload: { target_type: string; caliber_hint?: string; distance?: number; weapon_type?: string; holes: AcceptedHole[] },
): Promise<AcceptedResult> {
  const res = await fetch('/v1/scoring/results', {
    method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ job_id: jobId, ...payload }),
  });
  if (!res.ok) throw new Error(`POST /v1/scoring/results failed: ${res.status}`);
  return (await res.json()) as AcceptedResult;
}

export async function getAggregations(): Promise<Aggregations> {
  const res = await fetch('/v1/scores/aggregations', { headers: { Accept: 'application/json' } });
  if (!res.ok) throw new Error(`GET /v1/scores/aggregations failed: ${res.status}`);
  return (await res.json()) as Aggregations;
}
```

Also extend the `ScoringJob` interface to carry `distance?` + `weapon_type?`
(matching Phase 1.6's DTO changes) so the `/results/:jobId` screen can
pre-fill the accept form.

#### 6.5 `Results.tsx` — Accept/Reject buttons + accept form

**File**: `src/frontend/src/components/Results.tsx`

**Intent**: Today the results screen shows the marked image + per-hole dropdowns
(UI-only). Add an accept form (pre-filled with the wizard's params from the
`ScoringJob` DTO) + Accept/Reject buttons. Accept → POST + navigate to
`/dashboard`. Reject → navigate to `/dashboard` (no POST).

**Contract**: Below the holes list (after `:79`), add:
- A "Confirm parameters" section: caliber + distance + weapon_type + target_type
  inputs, pre-filled from `job` (the `ScoringJob` DTO now carries these from
  Phase 1.6). Editable so the user can correct pre-accept.
- An "Accept" button (`aria-label="Accept result"`) and a "Reject" button
  (`aria-label="Reject result"`).
- On Accept: build the corrected holes from the detector's `job.result.holes`
  with any dropdown corrections applied (the existing `corrections` state maps
  index → score; produce the final `AcceptedHole[]`). Call `acceptResult(
  jobId, { target_type, caliber_hint, distance, weapon_type, holes })`. On
  success, `navigate('/dashboard')`. On failure, show `role="alert"`.
- On Reject: `navigate('/dashboard')` directly (no POST). Optionally show a
  confirm dialog ("Discard this result?") — match the project's modal pattern
  (`NickPrompt.tsx`).

Remove the `// S-02: corrections are local state only` comment at `:80`.

#### 6.6 Dashboard components — swap mocks for real API

**Files**: `src/frontend/src/components/Dashboard.tsx`,
`HeroStats.tsx`, `ResultsList.tsx`, `DailyAverageChart.tsx`

**Intent**: The three dashboard components import from `mocks/dashboard.ts`.
Swap to `getAggregations()`.

**Contract**: In `Dashboard.tsx`, fetch aggregations on mount via a `useEffect`
+ `useState<Aggregations | null>` (loading state → `role="status"`; error →
`role="alert"`). Pass the aggregations (or loading/error) down to `HeroStats`,
`ResultsList`, `DailyAverageChart` as props. **Each child component is
rewritten, not just re-imported** (the DTO shapes differ from the fixture's —
see Phase 5.1): drop the `mocks/dashboard.ts` import and rebind to the new
props/field names + scale. `HeroStats` renders `hero.total_shots`,
`hero.last_session_average`, `hero.best_result` (all already 0-10/null). 
`ResultsList` renders `recent` and must adapt to the scale + name change: the
fixture's `ResultSummary.score` (0-100) becomes `ResultSummary.score_average`
(0-10) — the per-row display changes (e.g. "8.4" not "84", or a 0-10 label),
and each row links to `/results/{r.source_job}` (the original `ScoringJob` id,
so the user can re-view the detection). `DailyAverageChart` renders
`daily_averages` (already 0-10 in the fixture, so the axis is unchanged). 
Delete `src/mocks/dashboard.ts` after the swap (no remaining consumers —
verified). Update the existing `HeroStats`/`ResultsList`/`DailyAverageChart`
tests to the new prop shapes. Keep the loading/error fallbacks accessible.

#### 6.7 Component + client tests

**Files**: `src/frontend/src/components/Results.test.tsx` (extend),
`Dashboard.test.tsx` (extend), `HeroStats.test.tsx` / `ResultsList.test.tsx`
/ `DailyAverageChart.test.tsx` (extend), `src/frontend/src/api.test.ts` (extend)

**Intent**: Pin the new accept/reject UI + the dashboard API swap.

**Contract**: `Results.test.tsx`: add tests for the Accept button (spies on
`acceptResult`, asserts it's called with the corrected holes + params, asserts
navigation to `/dashboard`), the Reject button (asserts NO `acceptResult` call,
asserts navigation), and the param form (pre-fills from `job`, editable). The
existing "corrections stay local" test (`:92-103`) is updated to "corrections
flow into the accept payload." `Dashboard.test.tsx`: spy on `getAggregations`,
assert the three children render the fetched data, assert the loading +
error states. `api.test.ts`: tests for `acceptResult` (JSON body, CSRF header,
URL) + `getAggregations` (URL, Accept).

### Success Criteria:

#### Automated Verification:

- `cd src/frontend && npm run lint` passes
- `cd src/frontend && npx tsc --noEmit` passes
- `cd src/frontend && npm run test` passes (all migrated + new tests)
- `make check` passes
- `make fe-test` passes

#### Manual Verification:

- Full flow on desktop: dashboard → `/upload` → select caliber + distance +
  weapon_type + target_type → pick a file → `/waiting/:jobId` polls →
  `/results/:jobId` shows marked image + dropdowns + param form → Accept →
  `/dashboard` shows the updated hero stats + recent results list
- Reject path: same flow → Reject → `/dashboard` shows NO new entry
- Deep-link refresh on `/dashboard` re-fetches aggregations
- The dashboard's loading + error states render correctly (temporarily break
  the API to verify the `role="alert"` fallback)

**Implementation Note**: After completing this phase and all automated
verification passes, pause for manual confirmation from the human that the full
UI flow works before proceeding to the final verification phase.

---

## Phase 7: End-to-end verification + foundation docs

### Overview

Update `infrastructure.md` to reflect Railway/Tigris as prod (matching the
already-corrected `AGENTS.md §1`); add the Playwright E2E for the full
accept→dashboard-update flow; final `make check` + `be-test` + `fe-test` gate;
document the manual prod-deploy verification for Tigris.

### Changes Required:

#### 7.1 `infrastructure.md` — Railway/Tigris prod posture

**File**: `context/foundation/infrastructure.md`

**Intent**: AGENTS.md §1 was already corrected in the planning step; the
infrastructure doc still carries the S-02-era Railway/Tigris language but may
have stale entries. Reconcile.

**Contract**: Verify the Devil's Advocate weakness #3, the Pre-Mortem
narrative, and the Risk Register row reflect Railway Storage Buckets
(Tigris-backed) as the prod target (S-02 already updated these — confirm they
read coherently with Phase 4's presigned-URL policy + the OpenCV+S3 refactor
having landed). Add a note that the OpenCV-needs-local-bytes refactor landed in
S-03 (Phase 4) so the "S3 path raises NotImplementedError" risk is closed.

#### 7.2 Playwright E2E — full accept→dashboard flow

**File**: `src/frontend/tests-acceptance/accept-flow.spec.ts` (new)

**Intent**: The S-02 `scoring-flow.spec.ts` covers dashboard → upload → waiting
→ results. Extend to cover the S-03 accept→dashboard-update link.

**Contract**: A new spec (or extend `scoring-flow.spec.ts`) that continues
past `/results/:jobId`: fills the param form (or verifies it's pre-filled),
applies a hole correction, clicks Accept, asserts navigation to `/dashboard`,
asserts the hero stats reflect the newly-accepted result (total_shots
incremented, last_session_average updated), asserts the recent-results list
shows the new entry. Use the seeded `MOCK_DETECTOR_SEED` + `MOCK_DETECTOR_
HOLE_COUNT` from Phase 3 so the assertions are deterministic. Mirror the
existing Playwright conventions (`global-setup.ts` boots Django + Vite +
qcluster).

#### 7.3 Final verification gate

**Files**: no file changes — verification only.

**Intent**: The full gate before the change is considered done.

**Contract**: Run `make check` (lint + type-check + import contracts, be + fe),
`make be-test` (full backend suite), `make fe-test` (full frontend suite +
Playwright). All green. Manually verify the prod detector path: with
`VISION_DETECTOR=google` + `GOOGLE_API_KEY=<real key>` + `USE_S3=True` against
a real Tigris bucket (or MinIO as a stand-in if Tigris isn't provisioned
locally), confirm a real upload processes end-to-end. Document the Tigris-
specific verification as a manual prod-deploy gate (the actual prod deploy is
out of S-03 scope — that's the `/10x-deploy` chain).

### Success Criteria:

#### Automated Verification:

- `make check` passes
- `make be-test` passes (full backend suite)
- `make fe-test` passes (full frontend suite + Playwright, including the new
  accept-flow spec)
- `cd src/frontend && npx playwright test` passes (all specs green)

#### Manual Verification:

- `infrastructure.md` reads coherently with the corrected AGENTS.md §1 + the
  Phase 4 S3 refactor
- Full end-to-end flow on desktop (dashboard → upload → wizard → waiting →
  results → Accept → dashboard-updated) works against `MockDetector` +
  MinIO (`make dev-container`)
- Prod detector path: a real Google-detector upload processes end-to-end
  (manual, requires `GOOGLE_API_KEY` + a real S3-compatible endpoint; document
  the result in the change.md notes)

**Implementation Note**: After completing this phase and all automated
verification passes, pause for manual confirmation from the human that the
full manual testing (including the prod detector path) was successful before
considering this change done.

---

## Testing Strategy

### Unit Tests:

- **distance/weapon_type plumbing** (Phase 1.8): `schedule_image_processing`
  + `ScoringJob` row + `ScoringJobDTO` carry both fields end-to-end.
- **`accept_job` service** (Phase 2): creates `AcceptedResult` from a succeeded
  job + payload; idempotent on re-call; `PermissionError` on ownership
  mismatch; rejects non-succeeded jobs.
- **MockDetector random-N shape** (Phase 3.2): seeded determinism; hole count
  from env; each hole in valid ranges.
- **S3 storage methods** (Phase 4.4): `read_upload_bytes` + `write_deliverable_
  bytes` round-trip under mocked S3; `process_image` completes under mocked S3
  without `NotImplementedError`.
- **`aggregate_for_user`** (Phase 5.2): derived-session (calendar day) logic;
  total_shots / last_session_average / best_result computations; empty-user
  case.

### Integration / System Tests:

- **Accept route** (`tests/system/test_scoring_routes.py`, Phase 2.6): 201 on
  first accept, 200 on idempotent re-POST, 404 on ownership mismatch, 409 on
  non-succeeded job, 422 on empty holes.
- **Aggregation route** (`tests/system/test_aggregation_routes.py`, Phase 5.4):
  anon 401, empty user, multi-day user, same-day-derived-session, cross-user
  isolation.

### Frontend Tests:

- **api client** (`api.test.ts`, Phase 6.7): `acceptResult` JSON body + CSRF +
  URL; `getAggregations` URL + Accept; `createScoringJob` carries weapon_type.
- **Components** (Phase 6.7): `Results` Accept/Reject buttons (spy on
  `acceptResult`, assert navigation + payload), param form pre-fill; `Dashboard`
  fetches `getAggregations` + loading/error states; the three children render
  fetched data.

### Manual Testing Steps:

1. `make dev-container` → full stack up (web + worker + MinIO + create-bucket)
2. Log in via dev bypass; land on `/dashboard` (real aggregations now, empty
   for a fresh user)
3. Click add-photos → `/upload` → select caliber + distance + weapon_type +
   target_type → pick a fixture → `/waiting/:jobId` polls → `/results/:jobId`
   shows marked image + dropdowns + param form
4. Apply a hole correction → Accept → `/dashboard` shows the updated hero
   stats + recent results list
5. Repeat → Reject → `/dashboard` shows NO new entry
6. With `VISION_DETECTOR=google` + `GOOGLE_API_KEY` + real S3 endpoint: a real
   upload processes end-to-end (prod detector path)

## Performance Considerations

- **Aggregation on read** (Phase 5): each dashboard load runs 3–5 queries
  (count, max-date, recent, daily-averages-grouped). At MVP scale (one user,
  dozens of accepted results) this is trivial. If the dataset grows, a
  materialized stats table or cached aggregation is the follow-up — not S-03.
- **`process_image` tempfile** (Phase 4): the upload is now read into memory
  (`read_upload_bytes`) + written to a tempfile. The vision fixtures are
  ~100KB; real ISSF photos may be 5–10MB. The tempfile dance is bounded by
  disk, not memory (the bytes flow `S3 → memory → tempfile → cv2.imread`).
  S-02's `DATA_UPLOAD_MAX_MEMORY_SIZE` cap (set during impl-review F2) bounds
  the upload size at the Django layer.
- **Presigned URL expiry** (Phase 4): `AWS_QUERYSTRING_EXPIRE = 3600` (1 hour).
  If a user leaves the results screen open longer, the `<img>` breaks on
  refresh. Acceptable for MVP; a refresh-token-on-focus follow-up is post-MVP.
- **Polling** (unchanged): the 1500ms `/waiting/:jobId` poll is unchanged from
  S-02. The Google detector takes ~30s; the user sees `running` for the
  duration.

## Migration Notes

- **Two migrations land** (Phase 1.4 + Phase 2.2): `0004_scoringjob_distance_
  weapon_type` (AddField ×2, both nullable) + `0005_acceptedresult` (CreateModel).
  Both are additive; rollback is `migrate vision 0003`. No data migration.
- **`distance_m` → `distance` rename** (Phase 1.7): the BFF request field
  changes name. The SPA's `createScoringJob` is updated in Phase 6.3 in lockstep.
  No backwards-compat shim needed (single SPA consumer).
- **`MockDetector` output shape changes** (Phase 3): any persisted `ScoringJob`
  rows from S-02 (with the fixed 5-hole result JSON) are still readable —
  `_job_to_dto` rebuilds from the stored JSON regardless of detector. No
  migration needed.
- **`mocks/dashboard.ts` deletion** (Phase 6.6): the fixture file is deleted
  after the dashboard swap. No consumers remain after 6.6.

## References

- S-02 backbone (the change this builds on):
  `context/archive/2026-07-26-photo-detection-review/plan.md`,
  `plan-brief.md`, `research.md`
- S-02's deferred-to-S-03 register (the source of this plan's scope):
  `context/archive/2026-07-26-photo-detection-review/plan.md` "What We're NOT
  Doing" + the in-code `TODO(S-03)` markers
- Vision seam (Phase 2 + 4 consume):
  `src/domains/vision/services.py` (`schedule_image_processing` `:70`,
  `process_image` `:100`, `get_job` `:242`, `_job_to_dto` `:282`)
- Storage refactor point (Phase 4): `src/domains/vision/pipeline/storage.py:52-59,
  66-88, 104-123`; the call sites in `src/domains/vision/services.py:141, 146-147,
  159-168`
- BFF patterns (Phase 2 + 5 mirror): `src/bff/routers/scoring_routes.py`
  (the existing `session_auth` + `get_user_context` + `PermissionError → 404`
  + `@transaction.atomic` conventions)
- API-design lesson (Phase 2 + 5 endpoint naming):
  `context/foundation/lessons.md` "API endpoint URIs name resources, not actions"
- SPA patterns (Phase 6 mirrors): `src/frontend/src/components/Results.tsx`
  (the existing dropdown + fallback patterns), `Dashboard.tsx` (the grid +
  `useIsMobile` patterns), `api.ts` (the CSRF + multipart + JSON helpers)
- Mocked dashboard fixtures (Phase 6 swaps): `src/frontend/src/mocks/dashboard.ts`
  (the shapes the aggregation DTOs mirror)
- Detector factory (Phase 3): `src/domains/vision/detectors/factory.py:27-37`
  (`DetectorFactory.build` honored by `process_image` at `services.py:134`)
- Railway Storage Buckets (Tigris): `https://docs.railway.com/storage-buckets`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Foundation — fidelity posture + distance/weapon_type columns

#### Automated

- [x] 1.1 `uv run python src/manage.py makemigrations --check --dry-run vision` reports no pending migration — 553cef5
- [x] 1.2 `uv run python src/manage.py migrate` applies cleanly — 553cef5
- [x] 1.3 `uv run pytest src/domains/vision/tests/test_services_q2.py` passes (incl. new distance/weapon_type test) — 553cef5
- [x] 1.4 `uv run pytest tests/system/test_scoring_routes.py` passes (incl. new forwarding test) — 553cef5
- [x] 1.5 `make check` passes — 553cef5

#### Manual

- [ ] 1.6 PRD + roadmap edits read coherently (≥90% framed as deferred, not abandoned)
- [ ] 1.7 With `VISION_DETECTOR=mock make dev`, a POST carrying `distance` + `weapon_type` persists both on the ScoringJob row (verify via `/admin/vision/scoringjob/`)

### Phase 2: AcceptedResult model + accept/reject BFF routes

#### Automated

- [x] 2.1 `uv run python src/manage.py makemigrations --check --dry-run vision` reports no pending migration — 8f043dc
- [x] 2.2 `uv run python src/manage.py migrate` applies cleanly — 8f043dc
- [x] 2.3 `uv run pytest tests/system/test_scoring_routes.py` passes (incl. new accept-contract tests) — 8f043dc
- [x] 2.4 `make check` passes — 8f043dc
- [x] 2.5 `make be-test` passes (no regressions) — 8f043dc

#### Manual

- [ ] 2.6 With `VISION_DETECTOR=mock make dev`, after a job succeeds, `curl -X POST /v1/scoring/results` → 201; second POST → 200 with same `result_id`
- [ ] 2.7 The `AcceptedResult` row is visible in `/admin/vision/acceptedresult/`

### Phase 3: MockDetector rewrite (random N holes)

#### Automated

- [x] 3.1 `uv run pytest src/domains/vision/tests/test_mock_detector.py` passes (new random-N shape) — ae9a085
- [x] 3.2 `uv run pytest src/domains/vision/tests/test_services_q2.py` passes (migrated) — ae9a085
- [x] 3.3 `uv run pytest tests/system/test_scoring_routes.py` passes (migrated) — ae9a085
- [x] 3.4 `cd src/frontend && npx playwright test` passes (migrated scoring-flow.spec) — ae9a085
- [x] 3.5 `make check` passes — ae9a085

#### Manual

- [ ] 3.6 With `VISION_DETECTOR=mock make dev`, two consecutive jobs return DIFFERENT hole patterns; with `MOCK_DETECTOR_SEED=42`, they're identical

### Phase 4: S3-compatible storage refactor (the prod enabler)

#### Automated

- [x] 4.1 `uv run pytest src/domains/vision/tests/test_storage_swap.py` passes (new S3-path tests) — 9d56aa4
- [x] 4.2 `uv run pytest src/domains/vision/tests/test_services_q2.py` passes (process_image under mocked S3 completes) — 9d56aa4
- [x] 4.3 `make check` passes — 9d56aa4
- [x] 4.4 `make be-test` passes (no regressions in the FS path) — 9d56aa4

#### Manual

- [ ] 4.5 With `make dev-container` (MinIO + `USE_S3=True` + `VISION_DETECTOR=mock`), a POST → poll → succeeded round-trip completes WITHOUT `NotImplementedError`; the marked image is browser-fetchable
- [ ] 4.6 The `marked_image_url` from `GET /v1/scoring/jobs/{id}` is a signed URL (verify via `/admin/` or curl)

### Phase 5: Aggregation BFF route (`GET /v1/scores/aggregations`)

#### Automated

- [x] 5.1 `uv run pytest tests/system/test_aggregation_routes.py` passes (all cases) — 12439f6
- [x] 5.2 `make check` passes — 12439f6
- [x] 5.3 `make be-test` passes (no regressions) — 12439f6

#### Manual

- [ ] 5.4 After accepting a few results, `curl /v1/scores/aggregations` returns computed hero stats + recent + daily chart data
- [ ] 5.5 Derived-session logic correct: accept two results same day → `last_session_average` is their mean

### Phase 6: SPA — params + Accept/Reject on /results + dashboard swap

#### Automated

- [x] 6.1 `cd src/frontend && npm run lint` passes — e17be54
- [x] 6.2 `cd src/frontend && npx tsc --noEmit` passes — e17be54
- [x] 6.3 `cd src/frontend && npm run test` passes (migrated + new tests) — e17be54
- [x] 6.4 `make check` passes — e17be54
- [x] 6.5 `make fe-test` passes — e17be54

#### Manual

- [ ] 6.6 Full flow on desktop: dashboard → `/upload` → caliber+distance+weapon_type+target_type → file → `/waiting/:jobId` → `/results/:jobId` → Accept → `/dashboard` shows updated stats
- [ ] 6.7 Reject path: same flow → Reject → `/dashboard` shows NO new entry
- [ ] 6.8 Deep-link refresh on `/dashboard` re-fetches aggregations
- [ ] 6.9 Dashboard loading + error states render correctly (break the API to verify `role="alert"`)

### Phase 7: End-to-end verification + foundation docs

#### Automated

- [x] 7.1 `make check` passes
- [x] 7.2 `make be-test` passes (full backend suite)
- [x] 7.3 `make fe-test` passes (full frontend suite + Playwright, incl. new accept-flow spec)
- [x] 7.4 `cd src/frontend && npx playwright test` passes (all specs green)

#### Manual

- [ ] 7.5 `infrastructure.md` reads coherently with the corrected AGENTS.md §1 + Phase 4 S3 refactor
- [ ] 7.6 Full end-to-end flow on desktop works against `MockDetector` + MinIO (`make dev-container`)
- [ ] 7.7 Prod detector path: a real Google-detector upload processes end-to-end (manual, requires `GOOGLE_API_KEY` + real S3 endpoint; document result in change.md)
