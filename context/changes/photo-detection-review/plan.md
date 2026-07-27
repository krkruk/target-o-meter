# S-02 photo-detection-review Implementation Plan

## Overview

S-02 (`photo-detection-review`) connects the existing vision seam to its first
end-to-end consumer. A real BFF orchestration router wraps
`schedule_image_processing` / `get_job` / `reap_stuck_jobs`, exercised in dev via
the existing `MockDetector` (selected by a new `VISION_DETECTOR` env through the
existing `DetectorFactory`). The S-01 SPA gains React Router + recharts and a
single-screen-no-scroll dashboard plus a five-route capture/upload wizard.
Storage swaps from hardcoded `FileSystemStorage` to an env-driven default
(`USE_S3`), with a Tigris-in-prod / MinIO-locally posture. The Docker dev
environment deferred from F-01 lands here, extended with MinIO. Two foundation
docs (`infrastructure.md`, `AGENTS.md §1`) get updated to reflect Railway's new
Storage Buckets offering.

The user's "mock the data for now" directive is honored by routing the real
round-trip through `MockDetector` in dev (`VISION_DETECTOR=mock`,
`USE_S3=False`): the upload→poll→result flow is exercised against real storage
and real django-q2 — only the detector output is mocked. S-03 then flips both
flags (`VISION_DETECTOR=google`, `USE_S3=True`) for prod and confronts the
≥90% fidelity wedge.

## Current State Analysis

**Vision seam — already built and callable.** `schedule_image_processing`
(`src/domains/vision/services.py:69-96`) creates a `ScoringJob(status="queued")`
and enqueues `process_image` on django-q2 inside `transaction.atomic()`. `get_job`
(`:238-256`) is owner-only (raises `PermissionError` on mismatch OR `DoesNotExist`,
both map to 404). `reap_stuck_jobs` (`:204-235`) flips stale `RUNNING` rows to
`FAILED` — required for the PRD §Guardrail "no dead-end states."

**Three load-bearing gaps in the seam:**
1. `process_image` at `services.py:131` **hardcodes**
   `GoogleAIStudioDetector()` — it does NOT use `DetectorFactory`
   (`detectors/factory.py`). Shape A requires wiring the factory in (Phase 3).
2. `ScoringStorage` at `pipeline/storage.py:14,32` **hardcodes**
   `FileSystemStorage` and instantiates it directly; it does NOT consult
   `STORAGES["default"]`. Swapping settings alone has no effect on uploads
   (Phase 2).
3. The path-shaped methods (`absolute_path`, `write_deliverable`, `_safe_join`)
   use `Path.resolve()` + `relative_to()` — they break under S3. **Deferred to
   S-03** (when the q2 task body actually round-trips bytes); the dev path with
   `USE_S3=False` + `MockDetector` never exercises them against S3.

**Storage deps missing.** `django-storages` and `boto3` are NOT in
`pyproject.toml` (verified) and not installed (verified). `django-ninja` and
`django-q2` ARE installed and the existing code imports them.

**SPA from S-01 — what S-02 extends.** `App.tsx:6` explicitly comments
"React Router lands in S-02/S-03." `AppShell.tsx:32-36` has the dashboard
placeholder. `Sidebar.tsx:31` has an unwired `onHome`. `api.ts` has no multipart
helper. No router, chart lib, UI lib, or state lib in `package.json`. CSS Modules
convention (`Component.module.css`, camelCase keys, `data-*` state attrs); design
tokens once in `styles.css:16-24`; 760px mobile breakpoint convention
(`Welcome.module.css:87-92`); accessibility is load-bearing (roles + aria-labels
pinned by tests).

**BFF patterns.** `session_auth` (401 anon) + body-call `require_owner` (403
non-owner) at `bff/api.py:42-64`. Routers mount via `api.add_router("/", router)`
at `bff/urls.py:29-31` and pick up the `v1/` prefix at `:40`.

**Test patterns.** System tests live in `tests/system/` with
`pytestmark = [pytest.mark.django_db, pytest.mark.dev]`; two styles — Django test
client (fast) and live `runserver` subprocess via `runserver_factory` (blackbox,
real CSRF cookie path, needed for multipart). Auth via
`client.force_login(...)` or `DEV_AUTH_BYPASS_SUB` env on the live server. No
multipart test exists yet — S-02 writes the first. SPA component tests use
`vi.spyOn(api, '<fn>')` (NOT `vi.mock`), `beforeEach(() => vi.restoreAllMocks())`,
`@testing-library/react` accessible queries. `identity/test_utils.py` has the
seeders (`make_user`, `make_owner`); `vision/test_utils.py` is an empty stub.

### Key Discoveries:

- `process_image` hardcodes `GoogleAIStudioDetector` (`services.py:131`) — the
  factory (`detectors/factory.py`) is dead code in the prod path today. Phase 3
  wires it in via `VISION_DETECTOR` env.
- `ScoringStorage.__init__` (`storage.py:25-32`) takes an explicit `location`
  for tests/CLI and falls back to `MEDIA_ROOT/scoring` or `BASE_DIR/scoring_storage`.
  Phase 2 changes the default branch to consult `default_storage`.
- Railway Storage Buckets run on **Tigris, not MinIO** (verified via official
  docs). MinIO is still the right local-dev choice (S3-compatible parity). The
  prod-vs-local story is "Tigris in prod, MinIO locally" — NOT "Railway uses
  MinIO" as the brief stated. `infrastructure.md` and `AGENTS.md §1` both need
  updating.
- The q2 broker is SQLite (`Q_CLUSTER['orm']=='default'`, `settings.py:278`) —
  the enqueue is transactional. AGENTS.md §6.2 mandates the BFF-level
  `transaction.atomic` wrap; the service's own atomic block
  (`services.py:81`) is a nested savepoint. Both stay.
- `_job_to_dto` (`services.py:278-322`) raises `ValueError` on malformed result
  JSON. The BFF must treat `result` as nullable even on `succeeded` jobs.
- F-01's archived plan (`context/archive/2026-07-24-oauth-roles-scaffold/plan.md:468-524`)
  preserved a fully-specified Docker dev env (Dockerfile + dev compose +
  dev-seed + .dockerignore), deferred by plan-review F6. Phase 5 picks it up
  verbatim and extends with MinIO.
- The caliber/distance lists contradict AGENTS.md §2 (ISSF-only) and vision's
  `TargetType` Literal. The user has decided to use the lists as-is, and the
  taxonomy lives UI-only in S-02 (caliber as free-text `caliber_hint`, distance
  as a BFF-level mock field). The taxonomy bugs (`.32ACP` missing, `.22LR` /
  `9x19mm` silent DEFAULT fallback, `Slug` split-brain) are deferred to S-03
  where they become load-bearing.

## Desired End State

After S-02 lands:

1. **Backend** — A user can `POST /v1/scoring/jobs` with a multipart upload
   (image + `target_type` + `caliber_hint` + `distance_m`) and receive a
   `job_id`; poll `GET /v1/scoring/jobs/{job_id}` to watch the job progress
   through `queued → running → succeeded` and read back the `ScoringJobDTO`
   (with mocked holes from `MockDetector` in dev). Both Owner and User roles
   can upload; per-job ownership is enforced on read. The atomicity contract
   (AGENTS.md §6.2) holds: a failure after enqueue rolls the q2 task row back.
2. **Storage** — `STORAGES["default"]` is env-driven via `USE_S3`.
   `ScoringStorage.__init__` consults `default_storage`. Default dev path stays
   `USE_S3=False` (FS). The docker-compose MinIO path sets `USE_S3=True` and
   exercises the S3 backend against MinIO. (The path-shaped-methods refactor
   for OpenCV+S3 is S-03.)
3. **Dev environment** — `make dev-container` brings up web + worker + MinIO +
   create-bucket, live-reloading, seeded with dev admin/owner/user.
   `make prod-container` brings up a prod-shape stack (DEBUG=False, built
   frontend, gunicorn). `.env.example` documents every env var.
4. **SPA** — React Router mounts five routes (`/dashboard`, `/capture`,
   `/upload`, `/waiting/:jobId`, `/results/:jobId`). The dashboard is a
   viewport-locked single-screen CSS Grid (hero stats, add-photos button that
   branches to `/capture` or `/upload`, results list, daily-average-past-month
   recharts chart). The wizard collects caliber→distance→media; `/waiting/:jobId`
   polls and always resolves (stuck-job reaping); `/results/:jobId` shows the
   marked image with per-hole correction dropdowns (UI-only in S-02,
   persistence is S-03). Accessibility-first throughout.
5. **Foundation docs** — `infrastructure.md` no longer lists "No managed object
   storage" as a Railway weakness; the platform comparison, operational story,
   and risk register reflect Tigris-backed Storage Buckets. `AGENTS.md §1`
   describes S3-primary via django-storages with FS as debug fallback.

**Verification**: `make check` (lint + type-check + import contracts) passes;
`make be-test` passes (including the new scoring-routes system tests and the
detector-wiring regression test); `make fe-test` passes (including the new
component tests for dashboard, wizard, waiting, results);
`make dev-container` brings up the full stack; manual end-to-end upload → poll
→ result flow works in the browser against `MockDetector`.

## What We're NOT Doing

- **Closing the ≥90% fidelity wedge.** S-02 ships mocked results via
  `MockDetector`. The LLM detector's 0.638–0.799 Jaccard gap confronts S-03.
- **The S3-compatible path-shaped-methods refactor** (tempfile download before
  `cv2.imread`, prefix-based containment checks, deliverable upload-back). S-02
  lands only the config swap; the refactor lands in S-03 when q2 actually
  round-trips bytes through S3.
- **Vision-domain taxonomy work.** No changes to `caliber_taxonomy.py`, no new
  `distance` column on `ScoringJob`. The `.32ACP` / `.22LR` / `9x19mm` / `Slug`
  bugs stay deferred to S-03.
- **Persistence of accepted results, sessions, aggregation.** Roadmap S-03
  scope (FR-009, FR-010, FR-011, FR-012 aggregated). S-02's dashboard chart
  uses mocked fixtures because aggregation is S-03.
- **Per-hole correction persistence.** The dropdowns exist in the UI but
  corrections are not saved (FR-008 Socrates note: "v2 concern"; persistence is
  S-03).
- **Long-term image retention policy / privacy posture.** S-02 matches the
  existing vision posture (store uploads + deliverables). Roadmap Open Question
  #2 (score-only vs image storage) is deferred.
- **CI/CD pipeline, multi-region, HA.** Out of MVP scope per
  `infrastructure.md` §"Out of Scope".

## Implementation Approach

**Shape A (real BFF + MockDetector).** The BFF orchestrates the real vision
seam; in dev the detector is `MockDetector` (selected by `VISION_DETECTOR=mock`
through `DetectorFactory`). This makes S-03 a detector-env flip + the S3 path
refactor, not a BFF rewrite.

**Phase ordering rationale.** Phases 1-3 are small individually-shippable
backend changes that de-risk before the heavy phases: foundation doc updates +
deps must precede the storage code; the storage swap and detector wiring must
precede both the BFF router (which calls `save_upload` + `schedule_image_
processing`) and the Docker compose (which exercises them). Phase 4 (BFF) and
Phase 5 (Docker) are the backend/infra core. Phases 6-8 are pure frontend,
sequenced router → dashboard → wizard so each builds on the previous. Each
phase has automated + manual verification gates; manual gates pause for the
human before the next phase.

**Dev path (no API keys, no S3):** `USE_S3=False` + `VISION_DETECTOR=mock` →
`FileSystemStorage` + `MockDetector`. The full upload→poll→result round-trip is
real; only the detector output is mocked.

**Docker dev path (exercises S3 against MinIO):** `USE_S3=True` + MinIO vars +
`VISION_DETECTOR=mock` → django-storages S3 backend against MinIO +
`MockDetector`. The config-swap S3 path is exercised; the OpenCV-needs-local-
bytes path is NOT (that's S-03).

## Critical Implementation Details

- **Atomicity is two-layered and both layers stay.** `schedule_image_
  processing` wraps its own `transaction.atomic()` (`services.py:81`); AGENTS.md
  §6.2 mandates the BFF also wrap. The BFF's wrap is the outer transaction; the
  service's is a nested savepoint. Because the q2 broker is SQLite
  (`Q_CLUSTER['orm']=='default'`), the `async_task(...)` row lives in the same
  DB — if the BFF's outer transaction fails after the enqueue, the task row
  rolls back too. Do NOT remove either layer.
- **`USE_S3=False` must remain the default dev path.** The whole dev story
  (no AWS creds, no MinIO required for `make dev`) depends on FS being the
  default. `USE_S3` flips to `True` only inside the compose (MinIO present) or
  prod (Railway Tigris). Setting `USE_S3=True` without the S3 path refactor
  (Phase 2 only swaps config) means the q2 task body's `cv2.imread` would fail
  on a real upload — but the dev path uses `MockDetector` which ignores the
  image bytes, and the docker-compose path's `MockDetector` likewise never
  reaches the path-shaped methods. This is sound only because MockDetector
  short-circuits before storage is read for CV. S-03's real detector + S3 path
  MUST land the tempfile refactor together.
- **CSRF on multipart upload is different from JSON.** The SPA's existing
  `jsonHeaders()` helper sets `Content-Type: application/json` — do NOT reuse
  it for multipart (the browser must set the `boundary`). The new
  `createScoringJob` helper sets only `X-CSRFToken` and passes `body: FormData`.
- **Distance has no home in vision.** It is a BFF-level mock field on the
  request DTO (`distance_m: int | None`) and is NOT passed to
  `schedule_image_processing` (which has no distance param). S-03 promotes it
  to a real `ScoringJob.distance` column alongside FR-009.
- **`require_owner` is NOT used on the upload route.** Both Owner and User
  roles can upload per PRD FR-006/FR-007. `session_auth` (401 anon) is
  sufficient; `get_job` enforces per-job ownership.
- **`make dev` (the host target) and `make dev-container` coexist.** The host
  target stays the no-Docker fast loop; the container target is the
  prod-posture-mirroring stack. Both must keep working.

## Phase 1: Foundation doc updates + storage deps

### Overview

Update the two foundation docs that contradict the S3 swap, add the storage
dependencies, and write `.env.example` so the rest of the change has a stable
documented posture.

### Changes Required:

#### 1.1 `infrastructure.md` — remove the stale "No managed object storage" finding

**File**: `context/foundation/infrastructure.md`

**Intent**: Railway now offers Storage Buckets (Tigris-backed, S3-compatible).
The Devil's Advocate weakness #3 (`:61`), the Pre-Mortem narrative sentence
about "ephemeral filesystem forced an S3 integration" (`:67`), and the Risk
Register row "Uploaded target images lost on redeploy" (`:96`) all need
updating to reflect that Railway Storage Buckets are now the prod target.

**Contract**: Rewrite Devil's Advocate #3 to acknowledge Tigris-backed Storage
Buckets as the new prod target, noting the supported/unsupported features
(no SSE, no versioning, no object locks, no bucket lifecycle, no public access
— presigned URLs or backend proxying required for deliverable surfacing; no
native file-explorer UI). Update the Pre-Mortem narrative so the S3 integration
is planned, not forced-and-frictional. Replace the Risk Register row's
mitigation with "Railway Storage Buckets (Tigris-backed) for uploaded images +
deliverables; MinIO locally via docker-compose for S3-compatible dev parity."
Add a new "Storage" subsection under "Operational Story" covering bucket
creation, presigned URLs vs backend proxying, and the Tigris/MinIO split.
Update the `tech_stack` frontmatter to note S3-compatible object storage.

#### 1.2 `AGENTS.md §1` — S3-primary posture

**File**: `AGENTS.md`

**Intent**: §1 currently pins `FileSystemStorage` + hashed-path bucketing as
THE storage approach. Update to S3-primary (django-storages) with FS as the
debug fallback, so the architecture doc matches what S-02 ships.

**Contract**: Rewrite the "Storage" bullet in §1 to: "Storage: django-storages
S3 backend in prod (Railway Storage Buckets, Tigris-backed) and in docker-compose
dev (MinIO); `FileSystemStorage` is the env-selected debug fallback
(`USE_S3=False`). DB stores metadata only." Keep the "hashed path bucketing for
OpenCV binaries" detail — `ScoringStorage.save_upload`'s SHA-1 digest bucketing
still applies under both backends.

#### 1.3 `pyproject.toml` — add storage deps

**File**: `pyproject.toml`

**Intent**: Add `django-storages` and `boto3` so the S3 backend can be imported
and used. (`django-ninja` and `django-q2` are already installed transitively —
verify they're pinned explicitly while here, but do not change versions.)

**Contract**: Append `"django-storages>=1.14.4"` and `"boto3>=1.35"` to the
`dependencies = [...]` list. Add `"storages"` to `INSTALLED_APPS` in
`settings.py` (django-storages requires it). Run `uv lock` to refresh the
lockfile. The canonical Django 6.0 backend class is
`storages.backends.s3.S3Storage` (NOT the deprecated `S3Boto3Storage`).

#### 1.4 `.env.example` — extend with Storage + Detector commentary

**File**: `.env.example` (**extend** — already exists from F-01/S-01)

**Intent**: `.env.example` already carries the Auth section (`AUTH0_*`,
`OWNER_SUB_ID`, `DEV_AUTH_BYPASS_SUB`, `DEV_ADMIN_*`, `SECURE_COOKIES`,
`APP_BASE_URL`) and the Detector section (`GOOGLE_API_KEY`, `OLLAMA_HOST`,
`OLLAMA_MODEL`). This phase APPENDS the Storage section and adds the
`VISION_DETECTOR` value commentary (Phase 3.3 edits the same line). Do NOT
overwrite or reorder the existing sections — append only.

**Contract**: APPEND a new "Storage (S3 swap, S-02)" section to the existing
file: `USE_S3=False` (default; comment explaining when to flip —
docker-compose sets True against MinIO, prod sets True against Tigris),
`AWS_ACCESS_KEY_ID=` / `AWS_SECRET_ACCESS_KEY=` (MinIO: `minioadmin` /
`minioadmin`; prod: Tigris creds), `AWS_STORAGE_BUCKET_NAME=` (dev:
`target-o-meter-local`), `AWS_S3_ENDPOINT_URL=` (MinIO only:
`http://localhost:9000`; unset for Tigris), `AWS_S3_ADDRESSING_STYLE=path`
(MinIO; `auto` for Tigris). Then in the existing Detector section add a
comment line for `VISION_DETECTOR` (Phase 3.3 adds the value; Phase 1 just
reserves the section).

### Success Criteria:

#### Automated Verification:

- `uv lock` refreshes without error and the lockfile diff shows only the new
  deps + their transitive closure
- `uv run python -c "import storages, boto3; print(storages.__version__, boto3.__version__)"` succeeds
- `make check` passes (the new `INSTALLED_APPS` entry doesn't break import-linter)
- `uv run python src/manage.py check` passes (Django validates settings with
  storages in INSTALLED_APPS)

#### Manual Verification:

- The `infrastructure.md` and `AGENTS.md §1` edits read coherently — no
  leftover contradictions with the S3 swap
- `.env.example` covers every env var referenced in code (`grep -rn
  "os.environ" src/ | sort -u` matches)

**Implementation Note**: After completing this phase and all automated
verification passes, pause for manual confirmation from the human that the
manual verification was successful before proceeding to the next phase.

---

## Phase 2: Storage config swap (layers 1+2+3)

### Overview

Make `STORAGES["default"]` env-driven via `USE_S3`, and make
`ScoringStorage.__init__` consult `default_storage` when no explicit location
is passed. Leave the path-shaped methods untouched (S-03 scope).

### Changes Required:

#### 2.1 `settings.py` — env-driven STORAGES swap

**File**: `src/target_o_meter/settings.py`

**Intent**: Flip `STORAGES["default"]` between FS and S3 via a `USE_S3` env
bool, reusing the existing `_env_bool` helper (`:51-53`). When `USE_S3=True`,
read the AWS vars (with clear errors if absent). MinIO-vs-Tigris is controlled
by `AWS_S3_ENDPOINT_URL` (set for MinIO, unset for Tigris).

**Contract**: Replace the hardcoded `STORAGES` dict (`:332-339`) with:

```python
USE_S3 = _env_bool("USE_S3", False)
_default_backend = (
    "storages.backends.s3.S3Storage" if USE_S3
    else "django.core.files.storage.FileSystemStorage"
)
STORAGES = {
    "default": {"BACKEND": _default_backend},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
if USE_S3:
    AWS_ACCESS_KEY_ID = os.environ["AWS_ACCESS_KEY_ID"]
    AWS_SECRET_ACCESS_KEY = os.environ["AWS_SECRET_ACCESS_KEY"]
    AWS_STORAGE_BUCKET_NAME = os.environ["AWS_STORAGE_BUCKET_NAME"]
    AWS_S3_ENDPOINT_URL = os.environ.get("AWS_S3_ENDPOINT_URL")  # MinIO only
    AWS_S3_ADDRESSING_STYLE = os.environ.get("AWS_S3_ADDRESSING_STYLE", "auto")
    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = None
```

The `os.environ[...]` indexing (not `.get`) makes a missing prod var a loud
`KeyError` at startup instead of a silent misconfiguration.

#### 2.2 `ScoringStorage.__init__` — consult `default_storage`

**File**: `src/domains/vision/pipeline/storage.py`

**Intent**: When `ScoringStorage()` is constructed with no explicit `location`
(the production path from the q2 task body and the BFF), use Django's
`default_storage` so the env swap flows through. Keep the explicit-`location`
path for tests/CLI unchanged.

**Contract**: Change the `location is None` branch (`:28-31`) so that when
`USE_S3` is on, `self._storage` is `default_storage` (don't try to construct an
S3Storage manually — settings already did). When `USE_S3` is off, keep the
existing `MEDIA_ROOT/scoring` or `BASE_DIR/scoring_storage` fallback but
instantiate via `default_storage` too (so the swap is uniform). The
explicit-`location` branch keeps building a `FileSystemStorage(location=...)`
directly — that path is test/CLI-only and must stay FS. The cached `self._root`
(`:36`) only makes sense for the FS path; under S3, the path-shaped methods are
not called by S-02 (MockDetector short-circuits), so guard `_root`/`_safe_join`
to raise `NotImplementedError("S3 path ops land in S-03")` when the backend is
S3 — this makes any premature call loud rather than silently wrong.

#### 2.3 Settings + storage unit tests

**File**: `src/domains/vision/tests/test_storage_swap.py` (new)

**Intent**: Pin the env-driven swap so a future change can't silently regress
it.

**Contract**: Test cases — `USE_S3=False` (default) → `STORAGES["default"]
BACKEND` is FS, `ScoringStorage()` builds an FS-backed adapter, `save_upload`
round-trips bytes to disk. `USE_S3=True` with MinIO vars monkeypatched →
backend is `storages.backends.s3.S3Storage`. Missing `AWS_ACCESS_KEY_ID` with
`USE_S3=True` → settings import raises `KeyError`. Use
`pytest.MonkeyPatch.setenv`/`delenv` and `importlib.reload(settings)` to test
both branches without polluting other tests. Module marker
`pytestmark = pytest.mark.django_db` (no `.dev` — these are pure unit tests).

### Success Criteria:

#### Automated Verification:

- `uv run pytest src/domains/vision/tests/test_storage_swap.py` passes
- `make check` passes (import-linter still sees `vision` as independent)
- `uv run python src/manage.py check` passes with `USE_S3=False`
- `USE_S3=True AWS_ACCESS_KEY_ID=x AWS_SECRET_ACCESS_KEY=y
  AWS_STORAGE_BUCKET_NAME=z uv run python src/manage.py check` passes (settings
  import works; no actual S3 call made at check time)

#### Manual Verification:

- Start `make dev` (host path, `USE_S3=False`) — the existing vision seed/CLI
  path still writes to `scoring_storage/` on disk
- Confirm no regression in the existing `test_services_q2.py` (it constructs
  `ScoringStorage(location=tmp_path/"bucket")` — the explicit-location path is
  unchanged)

**Implementation Note**: After completing this phase and all automated
verification passes, pause for manual confirmation from the human that the
manual testing was successful before proceeding to the next phase.

---

## Phase 3: Detector env wiring

### Overview

Replace the hardcoded `GoogleAIStudioDetector()` in `process_image` with an
env-driven `DetectorFactory.build(VISION_DETECTOR)` call, defaulting to
`"google"` so prod behavior is unchanged. Document `VISION_DETECTOR` in
`.env.example` (Phase 1 already added the section; this phase adds the value
commentary).

### Changes Required:

#### 3.1 `process_image` — env-driven detector

**File**: `src/domains/vision/services.py`

**Intent**: `process_image` at `:131` hardcodes the detector. Replace with a
factory call driven by `VISION_DETECTOR` env (default `"google"`), so the dev
path can flip to `MockDetector` and S-03 can flip to `ollama` without code
changes.

**Contract**: At the top of the `try:` block in `process_image`, replace:

```python
detector = GoogleAIStudioDetector()
```

with:

```python
detector_name = os.environ.get("VISION_DETECTOR", "google")
detector = DetectorFactory.build(detector_name)
```

Add `import os` and `from src.domains.vision.detectors.factory import
DetectorFactory` to the module imports. Keep the `GoogleAIStudioDetector`
import only if still referenced elsewhere in the module (it is not after this
change — remove it to avoid an unused import; ruff will flag it). Update the
module docstring (`:9-10`) to reflect the env-driven selection.

#### 3.2 Regression test — google is still the default

**File**: `src/domains/vision/tests/test_services_q2.py`

**Intent**: Pin that `VISION_DETECTOR` unset (or set to `"google"`) builds a
`GoogleAIStudioDetector`, and `"mock"` builds a `MockDetector`. This guards
against accidentally changing the prod default.

**Contract**: Add two test functions. One asserts
`DetectorFactory.build(os.environ.get("VISION_DETECTOR", "google"))` is an
instance of `GoogleAIStudioDetector` (no env set). One monkeypatches
`VISION_DETECTOR=mock` and asserts the factory returns a `MockDetector`. These
can call `DetectorFactory.build` directly — no need to run the full pipeline.

#### 3.2a Mandatory rewrite — patch-target migration for the 4 existing tests

**File**: `src/domains/vision/tests/test_services_q2.py`

**Intent**: Phase 3.1 removes `GoogleAIStudioDetector` from `services.py`'s
imports, so `patch("src.domains.vision.services.GoogleAIStudioDetector", …)`
in the existing tests becomes a `AttributeError: <module> has no attribute
'GoogleAIStudioDetector'` at collection time. This rewrite is MANDATORY, not
optional — all four existing tests WILL break without it.

**Contract**: Migrate the four existing patch sites to patch
`src.domains.vision.services.DetectorFactory.build` instead, returning the
same instances the class-patch returned. Concretely:
- Line 66-67 (`test_process_image_writes_deliverables_and_marks_succeeded`):
  change `with patch("src.domains.vision.services.GoogleAIStudioDetector",
  return_value=MockDetector())` → `with patch(
  "src.domains.vision.services.DetectorFactory.build",
  return_value=MockDetector())`.
- Line 124 (`test_process_image_marks_failed_on_exception`): same swap.
- Line 148 (the third `GoogleAIStudioDetector`-patching test): same swap,
  preserving whatever `_Sentinel` / return-value shape that test uses.
- Line 201-202 (`test_process_image_is_idempotent_on_terminal_state`): same
  swap, preserving the `_Sentinel` instance.

The new patch target exercises the actual production wiring (Phase 3.1's
`DetectorFactory.build(...)` call) end-to-end, which the class-patch never
did. Add `from src.domains.vision.detectors.factory import DetectorFactory`
to the test module's imports if missing.

#### 3.3 `.env.example` — detector value commentary

**File**: `.env.example`

**Intent**: Make the `VISION_DETECTOR` line self-explanatory so a new
contributor knows the three valid values and which to use when.

**Contract**: Under the Detector section, list `VISION_DETECTOR=mock` as the
default in the example file with a comment: `# mock = dev (no API calls);
# google = prod (requires GOOGLE_API_KEY); ollama = local LLM (requires
# OLLAMA_HOST, OLLAMA_MODEL). DetectorFactory.build() honors these names.`

### Success Criteria:

#### Automated Verification:

- `uv run pytest src/domains/vision/tests/test_services_q2.py` passes
  (including the two new tests + the 3.2a rewrite of the 4 existing tests)
- `make check` passes (the removed `GoogleAIStudioDetector` import doesn't
  leave a dangling reference)
- The 4 existing `process_image` tests now patch
  `src.domains.vision.services.DetectorFactory.build` (mandatory per 3.2a —
  they WILL fail collection with `AttributeError` otherwise)

#### Manual Verification:

- With `VISION_DETECTOR=mock make dev`, calling
  `schedule_image_processing` (via the Django shell or, after Phase 4, the BFF)
  runs `MockDetector` and returns the fixed 5-hole pattern
- With `VISION_DETECTOR` unset, the existing prod-shape behavior is unchanged
  (no Google API call attempted without `GOOGLE_API_KEY` — confirmed by the
  existing test patching the detector)

**Implementation Note**: After completing this phase and all automated
verification passes, pause for manual confirmation from the human that the
manual testing was successful before proceeding to the next phase.

---

## Phase 4: BFF scoring routes (real, MockDetector-backed)

### Overview

New `src/bff/routers/scoring_routes.py` with two endpoints over the vision
seam: `POST /v1/scoring/jobs` (multipart upload, atomic) and
`GET /v1/scoring/jobs/{job_id}` (reap-then-read). Mount via `bff/urls.py`.

### Changes Required:

#### 4.0 `vision/dtos.py` + `vision/services._job_to_dto` — surface the marked image

**File**: `src/domains/vision/dtos.py`, `src/domains/vision/services.py` (`_job_to_dto`)

**Intent**: The `/results/:jobId` screen needs the marked image, but
`ScoringJobDTO` (`dtos.py:37-51`) has no field carrying it. The ORM row has
`marked_image_path` (`models.py:45`) but `_job_to_dto` (`services.py:278-322`)
never copies it onto the DTO. Add the field so Phase 8.5 has a contract to
consume — Phase 4 ships the GET carrying it.

**Contract**: Add `marked_image_url: Optional[str] = None` to `ScoringJobDTO`
(after `completed_at`). In `_job_to_dto`, populate it from
`job.marked_image_path` via `default_storage.url(...)` when the path is set
(lazy import `from django.core.files.storage import default_storage`; guard
`None` so jobs still in `queued`/`running` don't 500). Under `USE_S3=False`
dev this resolves to a `MEDIA_URL`-rooted URL the SPA can fetch directly;
under the docker-compose MinIO path it resolves to a MinIO URL (the
Tigris/prod presigned-URL policy is an S-03 concern when the OpenCV+S3
refactor lands). `marked_image_url` stays `None` until `process_image` writes
the deliverable and flips `status=SUCCEEDED` — Phase 8.5 treats `None` as
"results not yet available" and renders the same fallback as a null `result`.

#### 4.1 `scoring_routes.py` — the new router

**File**: `src/bff/routers/scoring_routes.py` (new)

**Intent**: First BFF orchestration over the vision seam. Honors AGENTS.md §6.2
(BFF wraps multi-domain work in `transaction.atomic`). Both roles can upload;
per-job ownership enforced on read.

**Contract**:

```python
from typing import Literal

from ninja import Router, Form, File, Schema
from ninja.files import UploadedFile
from ninja.errors import HttpError
from django.contrib.auth import get_user_model
from django.db import transaction

from src.bff.api import session_auth
from src.domains.identity.services import get_user_context
from src.domains.vision.pipeline.storage import ScoringStorage
from src.domains.vision.services import (
    schedule_image_processing,
    get_job,
    reap_stuck_jobs,
)
from src.domains.vision.dtos import ScoringJobDTO

router = Router()


class ScoringJobIn(Schema):
    target_type: Literal["air_pistol", "precision_pistol"] = "air_pistol"  # 422 on anything else
    caliber_hint: str | None = None   # free-text; UI taxonomy lives client-side
    distance_m: int | None = None     # BFF-level mock field; vision has no distance concept


class ScoringJobOut(Schema):
    job_id: str
    status: str


@router.post("/scoring/jobs", auth=session_auth, response={201: ScoringJobOut})
@transaction.atomic
def create_scoring_job(
    request,
    details: Form[ScoringJobIn],
    file: File[UploadedFile],
) -> ScoringJobOut:
    try:
        user_dto = get_user_context(str(request.user.sub))
    except get_user_model().DoesNotExist:
        raise HttpError(401, "Session user no longer exists") from None
    storage = ScoringStorage()
    input_path = storage.save_upload(file.read(), file.name)
    job_id = schedule_image_processing(
        user_uuid=user_dto.user_uuid,
        input_path=input_path,
        target_type=details.target_type,        # type narrowed by vision's TargetType
        caliber_hint=details.caliber_hint,
    )
    return ScoringJobOut(job_id=job_id, status="queued")


@router.get("/scoring/jobs/{job_id}", auth=session_auth, response={200: ScoringJobDTO})
def get_scoring_job(request, job_id: str) -> ScoringJobDTO:
    try:
        user_dto = get_user_context(str(request.user.sub))
    except get_user_model().DoesNotExist:
        raise HttpError(401, "Session user no longer exists") from None
    reap_stuck_jobs()   # PRD §Guardrail: no dead-end states
    try:
        return get_job(job_id, user_dto.user_uuid)
    except PermissionError:
        raise HttpError(404, "Not found") from None
```

`target_type` is typed as `Literal["air_pistol", "precision_pistol"]` on the
request DTO, so django-ninja/Pydantic rejects anything else with **422 at the
BFF boundary** — do NOT rely on the ORM's `CharField` (it has no `choices=`,
so without this Pydantic guard an invalid value saves cleanly and only blows
up inside `process_image` when the worker runs). The system-test matrix (4.3)
pins `"banana"` → 422. `distance_m` is intentionally NOT forwarded to
`schedule_image_processing` (which has no such param) — it is a BFF mock
field, dropped on the floor in S-02, promoted in S-03.

Both `create_scoring_job` and `get_scoring_job` wrap `get_user_context` in
`try/except get_user_model().DoesNotExist → HttpError(401, "Session user no
longer exists")` — this matches the convention in `session_routes.py:42-43,
59-60` and `api.py:58-61` so a deleted-between-auth-and-body session sub
(S-04 user deletion) returns 401 instead of an unhandled 500. The `404`
mapping for `PermissionError` from `get_job` (not 403) is correct: ID-probers
can't distinguish "exists, not mine" from "doesn't exist" (services.py:242-244).

#### 4.2 `bff/urls.py` — mount the router

**File**: `src/bff/urls.py`

**Intent**: Register `scoring_routes.router` the same way `session_router` and
`owner_router` are mounted (`:29-30`), so its paths land under `/v1/`.

**Contract**: Add `from src.bff.routers.scoring_routes import router as
scoring_router` and `api.add_router("/", scoring_router)` alongside the
existing two `add_router` calls. Routes become `/v1/scoring/jobs` (POST) and
`/v1/scoring/jobs/{job_id}` (GET) via the `v1/` prefix at `:40`.

#### 4.3 System tests — status-code matrix + atomicity + multipart

**File**: `tests/system/test_scoring_routes.py` (new)

**Intent**: Pin the route contract. This is the repo's first multipart system
test; mirror the two-style pattern from `test_auth_flow.py` (Django test client
for the status-code matrix) and `test_spa_auth_seam.py` (live `runserver` for
the real CSRF-cookie + multipart path).

**Contract**: Module marker `pytestmark = [pytest.mark.django_db,
pytest.mark.dev]`. Test cases:
- `POST /v1/scoring/jobs` anon → 401 (no `force_login`)
- `POST` authed (User role) with valid image bytes + form fields → 201, returns
  `{job_id, status: "queued"}`, and a `ScoringJob` row exists with the right
  `user_uuid` and `status="queued"`
- `POST` with missing file → 422 (django-ninja schema validation)
- `POST` with `target_type=banana` → 422 (the `Literal` on `ScoringJobIn`
  rejects it at the BFF boundary — guards against the silent-save/async-blowup
  that the ORM's choice-less `CharField` would otherwise allow)
- `POST` with `VISION_DETECTOR=mock` set in the env, then poll the created
  `job_id` via `GET` until `succeeded`; assert `result.holes` has 5 entries
  (MockDetector's fixed pattern)
- `GET /v1/scoring/jobs/{job_id}` for a job owned by another user → 404 (not
  403 — `PermissionError` mapping)
- `GET` for a nonexistent id → 404
- **Atomicity**: monkeypatch `schedule_image_processing` to raise after the
  enqueue; assert no `ScoringJob` row survives (the BFF's outer
  `transaction.atomic` rolled it back) — mirror the assertion shape at
  `test_services_q2.py:135-161`
- **Multipart live-server**: one test using `runserver_factory(extra_env=
  {"DEV_AUTH_BYPASS_SUB": "auth0|test", "VISION_DETECTOR": "mock"})`, seeding
  the CSRF cookie via `server.get("/")`, then `server.post("/v1/scoring/jobs",
  files=..., data=..., headers={"X-CSRFToken": token})` — assert 201. Mirror
  `test_spa_auth_seam.py:62-94`.

Use `Path("src/domains/vision/tests/fixtures/12.jpg").read_bytes()` for the
upload bytes (the existing versioned fixture). Seed users via
`identity/test_utils.py:make_user` + `make_owner` + the `owner_sub`/`user_sub`
fixtures from `tests/system/conftest.py:29-41`.

### Success Criteria:

#### Automated Verification:

- `uv run pytest tests/system/test_scoring_routes.py` passes (all cases above)
- `make check` passes (import-linter independence contract intact —
  `scoring_routes` imports from `bff`, `identity`, `vision` services/dtos only,
  no cross-domain ORM)
- `make be-test` passes (full backend suite, no regressions)

#### Manual Verification:

- With `VISION_DETECTOR=mock make dev`, `curl -F` a POST to
  `/v1/scoring/jobs` (after a login cookie) returns 201 + `job_id`; polling
  `GET` transitions `queued → running → succeeded` with 5 mocked holes
- The atomicity test's manual analog: killing the worker mid-process leaves
  no orphan `ScoringJob` row when the BFF route itself raises
- Both Owner and User roles can upload (manual role-switch via
  `DEV_AUTH_BYPASS_SUB`)

**Implementation Note**: After completing this phase and all automated
verification passes, pause for manual confirmation from the human that the
manual testing was successful before proceeding to the next phase.

---

## Phase 5: Docker dev environment (F-01 deferred + MinIO)

### Overview

Land the Docker dev environment F-01 deferred (Dockerfile + dev compose +
dev-seed + .dockerignore, verbatim from F-01's preserved spec) and extend it
with MinIO + create-bucket services. Add `make dev-container` and
`make prod-container` targets with proper env var wiring.

### Changes Required:

#### 5.1 Makefile — `dev-container` and `prod-container` targets

**File**: `Makefile`

**Intent**: One command to bring up the dockerized dev or prod-shape stack.
Follows the existing Makefile conventions (`.PHONY`, `## help` comment,
`@echo "▸ ..."` step-announce pattern).

**Contract**: Add `dev-container prod-container` to the `.PHONY` line (`:11`).
Add two targets:

```makefile
dev-container:  ## Bring up the dockerized dev stack (web + worker + MinIO + create-bucket) with live-reload
	@echo "▸ building images (first run is slow; opencv system deps bake in)"
	docker compose -f docker-compose.dev.yml up --build

prod-container:  ## Bring up a prod-shape stack (DEBUG=false, built frontend, gunicorn) in Docker
	@echo "▸ building prod images"
	docker compose -f docker-compose.prod.yml up --build
```

Both compose files read env from `.env` (Phase 1's `.env.example` is the
template; copy to `.env` for local use, gitignored).

#### 5.2 `Dockerfile`

**File**: `Dockerfile` (new)

**Intent**: A dev/prod image with Python 3.14, uv, and the opencv system deps
the vision domain needs. Per `infrastructure.md` Risk Register (OpenCV build
failures on Railpack), pre-build opencv into the image.

**Contract**: `FROM python:3.14-slim`. Install system deps for
`opencv-python-headless` (`libgl1`, `libglib2.0-0`, and any others the
`opencv-python-headless` wheel needs on slim). Install `uv` (copy from the
official copyuv image). `WORKDIR /app`. `COPY pyproject.toml uv.lock ./` then
`RUN uv sync --frozen` (Docker cache: deps install once; `src/` is bind-mounted
at runtime for live-reload, not copied). For the prod build stage, also
`COPY src/ ./src/` and build the frontend (`npm ci && npm run build` in
`src/frontend/`) so `collectstatic` has hashed assets. Entrypoint deferred to
compose.

#### 5.3 `docker-compose.dev.yml` — web + worker + minio + create-bucket

**File**: `docker-compose.dev.yml` (new)

**Intent**: One `make dev-container` gives a fully-seeded, live-reloading dev
environment that mirrors prod's S3 storage posture (against MinIO).

**Contract**: Four services sharing the same image:
- `web`: runs `uv run python src/manage.py runserver 0.0.0.0:8000 --noreload`
  (the dev-seed entrypoint runs `migrate` + seed first); bind-mounts `./src`
  and `./templates` for live-reload; exposes `:8000`; `depends_on` the `minio`
  healthcheck; env from `.env` plus `USE_S3=True`, `AWS_S3_ENDPOINT_URL=
  http://minio:9000`, `AWS_S3_ADDRESSING_STYLE=path`,
  `AWS_ACCESS_KEY_ID=minioadmin`, `AWS_SECRET_ACCESS_KEY=minioadmin`,
  `AWS_STORAGE_BUCKET_NAME=target-o-meter-local`, `VISION_DETECTOR=mock`,
  `DEBUG=True`, `DEV_AUTH_BYPASS_SUB` (so the app is usable without Auth0)
- `worker`: same image + bind-mounts; runs
  `uv run python src/manage.py qcluster`; same env (it's the worker that
  actually runs `process_image`); `depends_on` `web` (so migrate runs first)
- `minio`: `minio/minio:latest`; `command: server /data --console-address
  ":9001"`; ports `9000:9000` (S3 API) + `9001:9001` (console); env
  `MINIO_ROOT_USER=minioadmin`, `MINIO_ROOT_PASSWORD=minioadmin`; named volume
  `minio-data:/data`; healthcheck hitting `/minio/health/live`
- `create-bucket`: `minio/mc:latest`; `depends_on` minio healthy; idempotent
  `mc alias set` + `mc mb --ignore-existing local/target-o-meter-local`

Named volumes: `minio-data`, plus a `db-data` volume for SQLite mounted where
`DATABASES["NAME"]` points. The dev-seed entrypoint (5.4) runs as the `web`
container's command/entrypoint wrapper.

#### 5.4 `docker/dev-seed.sh` — idempotent admin/owner/user seeding

**File**: `docker/dev-seed.sh` (new)

**Intent**: Idempotent dev seed — safe to re-run on every `up`. Mirrors F-01's
preserved spec (`context/archive/2026-07-24-oauth-roles-scaffold/plan.md:494-498`).

**Contract**: Bash script invoked by the compose entrypoint. Runs `uv run
python src/manage.py migrate` unconditionally. Then a `manage.py shell`-invoked
seed (or a one-off management command) that: `create_superuser(sub=
DEV_ADMIN_SUB, nick=DEV_ADMIN_NICK, password=DEV_ADMIN_PASSWORD)` if not
exists; `get_or_create_user_by_sub(OWNER_SUB_ID)` (nick `"dev-owner"`);
`get_or_create_user_by_sub("dev-user-sub")` (nick `"dev-user"`). All idempotent
— `up` re-runs are safe. Execs into `uv run python src/manage.py runserver
0.0.0.0:8000` (web) or `uv run python src/manage.py qcluster` (worker) as the
last step depending on a `$SERVICE_ROLE` env var the compose sets.

#### 5.5 `docker-compose.prod.yml` — prod-shape stack

**File**: `docker-compose.prod.yml` (new)

**Intent**: A local prod-shape smoke stack (DEBUG=False, built frontend
collected via WhiteNoise, gunicorn instead of runserver, no live-reload
bind-mount). Used by `make prod-container` to reproduce prod serving-path bugs.

**Contract**: Same image (built with the prod stage that copies `src/` and
builds the frontend). Two services: `web` runs `gunicorn target_o_meter.wsgi
:application` (NOT `runserver`) with `DEBUG=False`; `worker` runs `qcluster`.
No MinIO — prod uses Railway Storage Buckets (Tigris), so this stack reads
real `AWS_*` vars from `.env` (or the user provides stubs pointing at a real
S3-compatible endpoint). No `DEV_AUTH_BYPASS_SUB` (prod has no bypass).
Mounts only the db volume; no `src/` bind-mount (the image baked the code).

#### 5.6 `.dockerignore`

**File**: `.dockerignore` (new)

**Intent**: Keep the build context lean.

**Contract**: Ignore `.venv/`, `node_modules/`, `.git/`, `*.sqlite3`,
`staticfiles/`, `resources/`, `cv/` (frozen sandbox), `context/` (docs, not
runtime), `results/`, `.env` (compose reads it from the host; the image
shouldn't bake secrets).

#### 5.7 `.gitignore` — `.env` and docker artifacts

**File**: `.gitignore`

**Intent**: `.env` (real values) must never be committed; `.env.example` (Phase
1) is the template.

**Contract**: Append `.env`, `db-data/`, `minio-data/`, `results/` (if not
already ignored).

### Success Criteria:

#### Automated Verification:

- `docker compose -f docker-compose.dev.yml config` validates (no YAML errors,
  env interpolation resolves)
- `docker compose -f docker-compose.prod.yml config` validates
- `docker build -t target-o-meter-dev .` succeeds
- `make check` passes (the new shell script, if Python-invoked, is linted;
  the Makefile changes don't break `make help`)

#### Manual Verification:

- `make dev-container` brings up `web` + `worker` + `minio` + `create-bucket`
  cleanly; the `create-bucket` service exits 0 after creating the bucket
- Editing a file in `src/` triggers a `runserver` reload (live-reload verified
  — note: `--noreload` is in the command, so the reload is via the
  dev-seed/entrypoint-watcher; if true live-reload is wanted, drop `--noreload`
  and document the dual-process wrinkle)
- `/admin/` is reachable; logging in as the seeded dev admin works; the seeded
  Owner + User rows are visible
- `/v1/scoring/jobs` POST works end-to-end against MinIO (USE_S3=True path) —
  file lands in the MinIO bucket (verify via the MinIO console at
  `http://localhost:9001`)
- `make prod-container` brings up the prod-shape stack; visiting `/` shows the
  SPA mounted (built bundle served via WhiteNoise)

**Implementation Note**: After completing this phase and all automated
verification passes, pause for manual confirmation from the human that the full
Docker dev + prod loops work before considering this phase done.

---

## Phase 6: SPA router + api client extensions

### Overview

Add `react-router-dom`, mount `<BrowserRouter>` in `App.tsx`, replace the
`AppShell` placeholder with `<Routes>`, extend `api.ts` with the multipart
upload + poll helpers, and wire the unwired `Sidebar.onHome`.

### Changes Required:

#### 6.1 `package.json` — add `react-router-dom` and `recharts`

**File**: `src/frontend/package.json`

**Intent**: S-02 needs a router (multi-screen wizard) and a chart lib (daily
average). Both are net-new deps.

**Contract**: Append `"react-router-dom": "^6.26.0"` and `"recharts": "^2.12.0"`
to `dependencies`. Run `npm install` (which updates `package-lock.json`). Pin
major versions matching the React 18.3 baseline.

#### 6.2 `App.tsx` — mount the router

**File**: `src/frontend/src/App.tsx`

**Intent**: Wrap the authed-branch `<AppShell>` in a `<BrowserRouter>` and let
routes own the main-area content. Update the `:6` comment ("React Router lands
in S-02/S-03") to "React Router landed in S-02."

**Contract**: Import `BrowserRouter` (or `HashRouter` if the Django index view
serves a single path — verify by checking `bff/views.py:18-21` and whether the
SPA needs to support deep links via a catch-all URL; prefer `BrowserRouter` +
a Django catch-all that serves `index` for client-side routes). The auth seam
stays as-is (`useState<Me|null>`, `getMe` on mount); only the authed branch
changes from `<AppShell me={me} onLogout={...}>` to
`<BrowserRouter><AppShell .../></BrowserRouter>`. Add a Django URL pattern (in
`bff/urls.py`) that serves `index` for all non-API, non-OIDC paths so client-
side deep links (`/dashboard`, `/waiting/:jobId`) work on refresh — but make
sure it does NOT shadow `/v1/...`, `/login`, `/callback`, `/logout`, `/admin/`,
or static URLs.

#### 6.3 `AppShell.tsx` — routes own the main

**File**: `src/frontend/src/components/AppShell.tsx`

**Intent**: Replace the placeholder `<div className={styles.placeholder}>`
(`:32-36`) with `<Routes>...</Routes>` so each route renders its component
inside the shell. The shell (TopBar + Sidebar + main) stays; only the main's
content is routed.

**Contract**: Add `react-router-dom`'s `Routes`/`Route` and import the new
page components (`Dashboard`, `Capture`, `Upload`, `Waiting`, `Results` — these
are created in Phases 7-8; for Phase 6, stub each with a placeholder `<div>`
and a `TODO` comment, then replace the stubs in the later phases). Wire
`Sidebar`'s unwired `onHome` (`Sidebar.tsx:31`) — pass `onHome={() =>
navigate('/dashboard')}` (or use `<Link to="/dashboard">` inside the Sidebar
itself, which is the more idiomatic router pattern).

#### 6.4 `api.ts` — multipart upload + poll helpers

**File**: `src/frontend/src/api.ts`

**Intent**: Add `createScoringJob` (multipart, sets only `X-CSRFToken`) and
`getScoringJob` (JSON GET). These are the SPA's seam onto the Phase 4 routes.

**Contract**: Add a `multipartHeaders()` helper that returns only
`{'X-CSRFToken': readCsrfToken()}` (NOT `Content-Type` — the browser sets the
boundary). Add types matching the BFF DTOs:

```ts
export type ScoringJobStatus = 'queued' | 'running' | 'succeeded' | 'failed';
export interface DetectedHole { x: number; y: number; score: number; confidence: number; caliber?: string | null; }
export interface ScoringResult { holes: DetectedHole[]; target_type: string; notes?: string | null; detector_name: string; }
export interface ScoringJob {
  job_id: string; status: ScoringJobStatus; target_type: string;
  caliber_hint?: string | null; result?: ScoringResult | null;
  error?: string | null; created_at?: string | null; completed_at?: string | null;
}

export async function createScoringJob(file: File, target_type: string, caliber_hint?: string, distance_m?: number): Promise<{ job_id: string; status: string }> {
  const form = new FormData();
  form.append('file', file);
  form.append('target_type', target_type);
  if (caliber_hint) form.append('caliber_hint', caliber_hint);
  if (distance_m != null) form.append('distance_m', String(distance_m));
  const res = await fetch('/v1/scoring/jobs', { method: 'POST', headers: multipartHeaders(), body: form });
  if (!res.ok) throw new Error(`POST /v1/scoring/jobs failed: ${res.status}`);
  return (await res.json()) as { job_id: string; status: string };
}

export async function getScoringJob(jobId: string): Promise<ScoringJob> {
  const res = await fetch(`/v1/scoring/jobs/${jobId}`, { headers: { Accept: 'application/json' } });
  if (!res.ok) throw new Error(`GET /v1/scoring/jobs/${jobId} failed: ${res.status}`);
  return (await res.json()) as ScoringJob;
}
```

#### 6.5 Component + client tests

**File**: `src/frontend/src/api.test.ts` (extend); `src/frontend/src/components/AppShell.test.tsx` (extend)

**Intent**: Pin the new helpers and the router mount.

**Contract**: In `api.test.ts`, add tests for `createScoringJob` (asserts the
fetch spy was called with a `FormData` body, NO `Content-Type` header, the
`X-CSRFToken` header present) and `getScoringJob` (asserts the URL + Accept).
Seed the CSRF cookie via `document.cookie = 'csrftoken=...; path=/'` per the
existing pattern (`api.test.ts:53`). In `AppShell.test.tsx`, add a test
asserting the router renders the dashboard route at `/dashboard` (use the
`MemoryRouter` + `render` pattern; the existing `window.location` escape hatch
at `api.test.ts:15-33` is the model if route-path assertions are needed).

### Success Criteria:

#### Automated Verification:

- `cd src/frontend && npm run lint` passes (tsc via vite build)
- `cd src/frontend && npx tsc --noEmit` passes
- `cd src/frontend && npm run test` passes (existing + new api/AppShell tests)
- `make check` passes (full gate)

#### Manual Verification:

- `make dev` (host path) — the SPA still mounts; navigating to `/dashboard`
  shows the (Phase 6 stub) dashboard; the Sidebar's Home button navigates to
  `/dashboard`
- Deep-link refresh on `/dashboard` works (the Django catch-all serves index;
  the router picks up the path)
- The api client's `createScoringJob` posts a real multipart request (verify
  in the browser devtools network tab against the Phase 4 route)

**Implementation Note**: After completing this phase and all automated
verification passes, pause for manual confirmation from the human that the
manual testing was successful before proceeding to the next phase.

---

## Phase 7: Single-screen dashboard

### Overview

Replace the AppShell main with a viewport-locked CSS Grid dashboard: hero
stats, add-photos button (branches to `/capture` or `/upload` by viewport),
results list, and a daily-average-past-month recharts chart. The chart and
results use mocked fixtures (aggregation is S-03).

### Changes Required:

#### 7.1 `Dashboard.tsx` + `Dashboard.module.css` — the grid

**File**: `src/frontend/src/components/Dashboard.tsx` (new), `Dashboard.module.css` (new)

**Intent**: Deliver the brief's "all in one screen, no scroll" on laptop
viewports. The hardest layout problem in S-02.

**Contract**: `Dashboard.module.css` defines a viewport-locked grid:
`.dashboard { height: 100vh; display: grid; grid-template-rows: auto 1fr auto;
grid-template-columns: ... ; overflow: hidden; gap: ...; }` with named grid
areas for `hero`, `addPhotos`, `results`, `chart`. Use the existing design
tokens (`var(--color-bg)`, `var(--color-primary)`, etc. from `styles.css:16-24`).
Add a `@media (max-width: 760px)` block (the project's de-facto mobile
threshold per `Welcome.module.css:87-92`) that switches the grid to a
scrollable single column — mobile cannot honor "no scroll" and falls back
gracefully. `Dashboard.tsx` renders the four regions as children; the add-photos
button branches: on viewports ≤ 760px it navigates to `/capture` (mobile camera
capture), else `/upload` (PC file picker) — use a `matchMedia('(max-width:
760px)')` check. Component is accessible: `role="region"` + `aria-label` on
each region; the add-photos button has a clear `aria-label`.

#### 7.2 `HeroStats.tsx` — top-line numbers

**File**: `src/frontend/src/components/HeroStats.tsx` (new)

**Intent**: The brief's "hero stats" — total shots, last session, best result.
Mocked values in S-02 (no aggregation backend until S-03).

**Contract**: Reads from a `src/mocks/dashboard.ts` fixture module (typed,
deterministic values). Three stat cards in a row; accessible labels.

#### 7.3 `ResultsList.tsx` — recent results

**File**: `src/frontend/src/components/ResultsList.tsx` (new)

**Intent**: The brief's "results list" — recent scored targets with per-hole
correction dropdowns (the dropdowns are the per-hole correction UI; their
persistence is S-03). Mocked in S-02.

**Contract**: Reads from `src/mocks/dashboard.ts`. Each result row shows date,
score, target thumbnail (placeholder), and a affordance to drill into
`/results/:jobId`. Per-hole correction dropdowns live in `/results/:jobId`
(Phase 8), not in this list — the list is a summary.

#### 7.4 `DailyAverageChart.tsx` — recharts

**File**: `src/frontend/src/components/DailyAverageChart.tsx` (new)

**Intent**: The brief's "daily-average chart for the past month." First recharts
usage in the project.

**Contract**: A recharts `<LineChart>` (or `<AreaChart>`) with 30 daily data
points from `src/mocks/dashboard.ts`. Axis labels, grid, tooltip. Sized to fill
its grid area (responsive container). Accessible: a `role="img"` wrapper with
an `aria-label` summarizing the chart data (recharts SVGs are not screen-reader-
friendly by default).

#### 7.5 `src/mocks/dashboard.ts` — mocked fixtures

**File**: `src/frontend/src/mocks/dashboard.ts` (new)

**Intent**: One typed fixture module so the dashboard's mocked data is
centralized and S-03 can swap it for real API calls cleanly.

**Contract**: Exports `mockHeroStats`, `mockResults`, `mockDailyAverages` —
typed against the same interfaces the eventual S-03 API will return (so the
swap is a one-line import change per consumer).

#### 7.6 Component tests

**File**: `src/frontend/src/components/Dashboard.test.tsx`, `HeroStats.test.tsx`, `ResultsList.test.tsx`, `DailyAverageChart.test.tsx` (all new)

**Intent**: Pin the dashboard renders without scroll on laptop, falls back to
scroll on mobile, and the add-photos button branches correctly.

**Contract**: Follow the S-01 conventions — `vi.spyOn` on the api module only
if a component calls the api (the mocked dashboard doesn't, so these tests
just render + assert on accessible queries). `Dashboard.test.tsx` mocks
`window.matchMedia` (jsdom doesn't implement it) to test the desktop vs mobile
branch of the add-photos button. Assert `role="region"` + `aria-label` on each
region. Assert the chart wrapper has `role="img"`.

### Success Criteria:

#### Automated Verification:

- `cd src/frontend && npm run test` passes (all new component tests)
- `cd src/frontend && npx tsc --noEmit` passes
- `make check` passes

#### Manual Verification:

- On a 1920×1080 viewport: the dashboard renders all four regions with no
  scroll bar; resizing to 1366×768 still fits
- On a ≤760px viewport: the grid switches to a single scrollable column (the
  brief's "no scroll" is a laptop constraint; mobile falls back per the
  convention)
- The add-photos button routes to `/capture` on mobile, `/upload` on desktop
- The recharts chart renders with axes + tooltip

**Implementation Note**: After completing this phase and all automated
verification passes, pause for manual confirmation from the human that the
manual testing was successful before proceeding to the next phase.

---

## Phase 8: Capture/upload wizard + waiting + results

### Overview

Build the four remaining routes: `/capture` (mobile camera), `/upload` (PC file
picker), `/waiting/:jobId` (polls until terminal), `/results/:jobId` (marked
image + per-hole correction dropdowns). Caliber→distance sub-steps live inside
`/capture` and `/upload`. Accessibility-first throughout.

### Changes Required:

#### 8.1 `CaliberDistanceStep.tsx` — shared wizard step

**File**: `src/frontend/src/components/CaliberDistanceStep.tsx` (new)

**Intent**: The brief's "caliber→distance→camera wizard" — caliber + distance
selection is a shared step before media acquisition. Lives inside both
`/capture` and `/upload`.

**Contract**: Two dropdowns (caliber from the user's list
`.22LR / 9x19mm / .223Rem / .32ACP / 7.62x39 / Slug`; distance from
`7m / 15m / 25m / 50m / 100m / 200m / 300m / 500m`). The lists live in a
typed `src/taxonomy.ts` module (UI-only — the BFF treats caliber as free-text
`caliber_hint` and distance as the mock `distance_m` field). Accessible
`<select>` elements with `<label>`s. On "Next," stores the selections in
component state and advances to the media-acquisition step.

#### 8.2 `Capture.tsx` — mobile camera capture

**File**: `src/frontend/src/components/Capture.tsx` (new), route `/capture`

**Intent**: Mobile camera capture via native `<input type="file"
accept="image/*" capture="environment">` (no library needed per research §5).

**Contract**: Renders `<CaliberDistanceStep>` first, then the capture input.
On file selection, calls `createScoringJob(file, target_type, caliber_hint,
distance_m)` (Phase 6.4) and navigates to `/waiting/:jobId` with the returned
`job_id`. Accessible labels; the capture input has a clear call-to-action.

#### 8.3 `Upload.tsx` — PC file picker

**File**: `src/frontend/src/components/Upload.tsx` (new), route `/upload`

**Intent**: PC file picker — same flow as Capture minus the `capture` attribute.

**Contract**: Same shape as `Capture` but `<input type="file" accept="image/*">`
(no `capture`). Same `createScoringJob` → `/waiting/:jobId` navigation.

#### 8.4 `Waiting.tsx` — poll until terminal

**File**: `src/frontend/src/components/Waiting.tsx` (new), route `/waiting/:jobId`

**Intent**: Polls `getScoringJob(jobId)` (Phase 6.4) until `status` is
`succeeded` or `failed`, then navigates to `/results/:jobId` (on success) or
shows an error state (on failure). The "always resolves" guarantee comes from
the BFF calling `reap_stuck_jobs()` on every poll (Phase 4.1).

**Contract**: `useEffect` with a polling interval (e.g. 1500ms) that calls
`getScoringJob` and clears on unmount or terminal status. Status state machine:
`queued` (initial) → `running` (spinner) → `succeeded` (navigate) | `failed`
(error UI). Accessible: `role="status"` for the polling indicator,
`role="alert"` for the error state — mirror the S-01 conventions
(`App.tsx:28`, `NickPrompt.tsx:63`).

#### 8.5 `Results.tsx` — marked image + per-hole correction dropdowns

**File**: `src/frontend/src/components/Results.tsx` (new), route `/results/:jobId`

**Intent**: The brief's "results list with per-hole correction dropdowns."
Fetches the `ScoringJob` and renders the marked image with each hole's score;
each hole has a dropdown to correct the score (UI-only in S-02 — persistence is
S-03).

**Contract**: Calls `getScoringJob(jobId)`; if `result` is null (the
`_job_to_dto` fragility at `services.py:278-322` can produce a null result
even on `succeeded`) OR `marked_image_url` is null/empty, render an "unable to
load results" state. Otherwise render the marked image from
`marked_image_url` (Phase 4.0 surfaces this on the DTO; under `USE_S3=False`
dev it's a `MEDIA_URL`-rooted URL the SPA can fetch directly; under the
docker-compose MinIO path it's a MinIO URL — both browser-fetchable as-is,
no separate backend route needed in S-02). Each hole gets a
`<select>` of scores 0-10 + X; the selection updates local component state
only (no API call in S-02).

#### 8.6 Component tests

**File**: `src/frontend/src/components/{Capture,Upload,Waiting,Results,CaliberDistanceStep}.test.tsx` (all new)

**Intent**: Pin the wizard flows and the polling state machine.

**Contract**: Follow S-01 conventions (`vi.spyOn(api, '<fn>')`,
`beforeEach(() => vi.restoreAllMocks())`, accessible-query assertions).
`Waiting.test.tsx` tests the state machine: `queued` → `running` → `succeeded`
(mock `getScoringJob` to return successive states via `mockResolvedValueOnce`
chaining) and asserts navigation to `/results/:jobId`; the `failed` path shows
`role="alert"`. `Results.test.tsx` tests both the populated-result render
(per-hole dropdowns present) and the null-result fallback.

### Success Criteria:

#### Automated Verification:

- `cd src/frontend && npm run test` passes (all wizard component tests)
- `cd src/frontend && npx tsc --noEmit` passes
- `make check` passes
- `make fe-test` passes (the full frontend gate)

#### Manual Verification:

- Full flow on desktop: dashboard → `/upload` → select caliber + distance →
  pick a file → `/waiting/:jobId` polls → `/results/:jobId` shows the marked
  image with 5 mocked holes + correction dropdowns
- Full flow on mobile (or 760px viewport): dashboard → `/capture` → camera
  input → same waiting → results
- `failed` state: with `VISION_DETECTOR=mock` it's hard to force a failure;
  verify the error UI by temporarily breaking the detector. Two ways to force
  it: (a) set `VISION_DETECTOR=ollama` AND point `OLLAMA_HOST` at a guaranteed-
  dead port (e.g. `http://127.0.0.1:9`) — the detector constructs fine
  (`OllamaDetector.__init__` has a `DEFAULT_HOST` fallback and does NOT raise
  on a missing env var), then `detect()` fails on connection-refused and
  `process_image`'s except block flips the job to `failed`; (b) set
  `VISION_DETECTOR=banana` — `DetectorFactory.build` raises `ValueError` on
  unknown names immediately at `process_image`, → `failed` quickly and
  deterministically (preferred for a fast manual check).
- Refresh on `/waiting/:jobId` resumes polling (bookmarkable)
- Refresh on `/results/:jobId` re-fetches and re-renders

**Implementation Note**: After completing this phase and all automated
verification passes, pause for manual confirmation from the human that the full
end-to-end manual flow works before considering this change done.

---

## Testing Strategy

### Unit Tests:

- **Storage swap** (`src/domains/vision/tests/test_storage_swap.py`, Phase 2):
  `USE_S3=False` default branch; `USE_S3=True` MinIO branch; missing-AWS-var
  loud failure.
- **Detector wiring** (`test_services_q2.py`, Phase 3): `VISION_DETECTOR`
  unset → `GoogleAIStudioDetector`; `VISION_DETECTOR=mock` → `MockDetector`.
- **Atomicity** (`tests/system/test_scoring_routes.py`, Phase 4): BFF-level
  rollback when `schedule_image_processing` raises after enqueue.

### Integration / System Tests:

- **Scoring routes** (`tests/system/test_scoring_routes.py`, Phase 4): full
  status-code matrix (201/401/404/422), per-job ownership (404 not 403),
  MockDetector-backed round-trip (`queued → running → succeeded` with 5
  holes), multipart via live `runserver` + `DEV_AUTH_BYPASS_SUB`.

### Frontend Tests:

- **API client** (`api.test.ts`, Phase 6): `createScoringJob` multipart body +
  CSRF header + NO `Content-Type`; `getScoringJob` URL + Accept.
- **Components** (Phases 6-8): router mount, dashboard grid + mobile fallback,
  add-photos branch, wizard step, waiting state machine (queued/running/
  succeeded/failed), results render + null-result fallback.

### Manual Testing Steps:

1. `make dev-container` → full stack up; MinIO console shows the bucket
2. Log in via dev bypass; land on `/dashboard` (no scroll on laptop)
3. Click add-photos → `/upload` (desktop) → pick a fixture image →
   `/waiting/:jobId` polls → `/results/:jobId` shows 5 mocked holes
4. Repeat on a 760px viewport → add-photos routes to `/capture`
5. `make prod-container` → DEBUG=False; SPA mounts from the built bundle

## Performance Considerations

- **Polling interval** (`/waiting/:jobId`): 1500ms balances responsiveness
  against SQLite/MinIO load. MockDetector completes fast; real detector (S-03)
  takes ~30s, so the user sees `running` for the duration — the waiting screen
  must communicate progress, not just spin silently.
- **recharts** renders 30 daily points — trivial. S-03's aggregation views may
  need virtualization if the dataset grows.
- **Multipart upload size**: the vision fixtures are small (~100KB). Real ISSF
  photos may be 5-10MB; django-ninja's `UploadedFile.read()` loads the whole
  file into memory. For S-02's mocked path this is fine; S-03 should consider
  chunked writing if memory becomes a constraint under concurrent uploads
  (AGENTS.md §2 caps at 3 concurrent processing tasks).
- **docker-compose dev**: the `--noreload` flag on `runserver` means
  live-reload is via the entrypoint watcher (or document dropping `--noreload`
  for true autoreload — but then the dual-process wrinkle in the web container
  needs handling).

## Migration Notes

- **No data migration.** S-02 adds no model fields. The `ScoringJob` schema is
  unchanged. The `distance_m` field lives only on the BFF request DTO and is
  dropped on the floor.
- **No irreversible changes.** All Phase 1-3 changes are config/code; rollback
  is `git revert`. The `STORAGES` swap is env-gated; the default
  (`USE_S3=False`) preserves the pre-S-02 behavior exactly.
- **Foundation doc edits** (`infrastructure.md`, `AGENTS.md §1`) are
  reversible by revert; they are not migrations.

## References

- Related research: `context/changes/photo-detection-review/research.md`
- F-01's preserved Docker spec (Phase 5 starting point):
  `context/archive/2026-07-24-oauth-roles-scaffold/plan.md:468-524`
- S-01's SPA scaffold (Phase 6-8 starting point):
  `src/frontend/src/App.tsx`, `components/AppShell.tsx`, `Sidebar.tsx`, `api.ts`
- Vision seam (Phase 4 consumes):
  `src/domains/vision/services.py:69-96` (`schedule_image_processing`),
  `:238-256` (`get_job`), `:204-235` (`reap_stuck_jobs`)
- Storage swap point (Phase 2): `src/domains/vision/pipeline/storage.py:14,25-32`
- Detector wiring point (Phase 3): `src/domains/vision/services.py:131`,
  `src/domains/vision/detectors/factory.py`
- BFF patterns (Phase 4 mirrors): `src/bff/api.py:42-64`,
  `src/bff/routers/session_routes.py`, `bff/urls.py:29-31`
- System test patterns (Phase 4 mirrors): `tests/system/test_auth_flow.py`,
  `tests/system/test_spa_auth_seam.py`, `tests/system/conftest.py:82-260`
- SPA test patterns (Phase 6-8 mirror): `src/frontend/src/test-setup.ts`,
  `api.test.ts:15-33,53-60`, `components/AppShell.test.tsx`
- Storage taxonomy bugs deferred to S-03 (research §3):
  `src/domains/vision/pipeline/caliber_taxonomy.py:16-32`
- Railway Storage Buckets (Tigris, not MinIO):
  `https://docs.railway.com/storage-buckets`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Foundation doc updates + storage deps

#### Automated

- [x] 1.1 `uv lock` refreshes cleanly with django-storages + boto3 — 0c1ceda
- [x] 1.2 `import storages, boto3` succeeds in a fresh `uv run python` shell — 0c1ceda
- [x] 1.3 `make check` passes with storages in INSTALLED_APPS — 0c1ceda
- [x] 1.4 `uv run python src/manage.py check` passes — 0c1ceda

#### Manual

- [x] 1.5 infrastructure.md and AGENTS.md §1 edits read coherently (no leftover contradictions) — 0c1ceda
- [x] 1.6 `.env.example` covers every env var referenced in code — automated as `tests/system/test_env_example_coverage.py` (system test standing in for the manual grep) — 0c1ceda

> **Plan adjustment:** the AWS_* `KEY=` lines from §1.4 are documented as
> comments in `.env.example` here and activated as real `KEY=` lines in Phase 2,
> exactly when `settings.py` reads them. The repo's existing
> `test_env_example_keys_are_read_somewhere` guardrail enforces *declared ⟺
> read*; activating them in Phase 1 would leave them unread until Phase 2 and
> trip that guardrail. `USE_S3=False` ships active (its token already appears in
> `settings.py`).

### Phase 2: Storage config swap (layers 1+2+3)

#### Automated

- [x] 2.1 `uv run pytest src/domains/vision/tests/test_storage_swap.py` passes — 12cf71a
- [x] 2.2 `make check` passes — 12cf71a
- [x] 2.3 `manage.py check` passes with `USE_S3=False` — 12cf71a
- [x] 2.4 `manage.py check` passes with `USE_S3=True` + MinIO vars monkeypatched — 12cf71a

#### Manual

- [x] 2.5 `make dev` (host path) still writes to `scoring_storage/` on disk — covered at unit tier by `test_default_no_location_writes_under_media_root_scoring` (the no-location default branch; a blackbox `ScoringStorage()` would pollute `src/scoring_storage/` since `MEDIA_ROOT` isn't env-driven, so the system-test skill's "unit-level repro preferred" applies) — 12cf71a
- [x] 2.6 No regression in existing `test_services_q2.py` — full `be-test` green (117 passed) — 12cf71a

### Phase 3: Detector env wiring

#### Automated

- [x] 3.1 `uv run pytest src/domains/vision/tests/test_services_q2.py` passes (incl. 2 new tests) — 718e612
- [x] 3.2 4 existing tests migrated to patch `DetectorFactory.build` (Phase 3.2a — mandatory, otherwise `AttributeError`) — the file had **3** patch sites (not 4); all 3 migrated — 718e612
- [x] 3.3 `make check` passes (no dangling GoogleAIStudioDetector import) — 718e612

#### Manual

- [x] 3.4 `VISION_DETECTOR=mock make dev` runs MockDetector via the factory — automated as `tests/system/test_detector_env_wiring.py::test_vision_detector_mock_runs_mockdetector_via_factory` (drives `process_image` via `manage.py shell` subprocess; asserts 5-hole pattern + no traceback) — 718e612
- [x] 3.5 `VISION_DETECTOR` unset leaves prod-shape behavior unchanged — automated as `tests/system/test_detector_env_wiring.py::test_vision_detector_unset_boots_prod_shape` (runserver boots clean); the unset→google default is unit-pinned by `test_factory_default_is_google` — 718e612

### Phase 4: BFF scoring routes (real, MockDetector-backed)

#### Automated

- [x] 4.1 `uv run pytest tests/system/test_scoring_routes.py` passes (all cases) — 3aec152
- [x] 4.2 `make check` passes (import-linter independence intact) — 3aec152
- [x] 4.3 `make be-test` passes (no regressions) — 3aec152

#### Manual

- [x] 4.4 `curl -F` POST to `/v1/scoring/jobs` returns 201 + job_id; poll transitions to succeeded with 5 mocked holes — automated as `tests/system/test_scoring_routes.py::test_post_scoring_jobs_creates_job_for_user_role` + `::test_get_scoring_job_returns_marked_image_url_when_succeeded` + `::test_post_scoring_jobs_works_on_live_runserver` (real WSGI multipart; the MockDetector round-trip drives `process_image` and asserts 5 holes + status=succeeded) — 3aec152
- [x] 4.5 Atomicity: BFF raise after enqueue leaves no orphan ScoringJob row — automated as `tests/system/test_scoring_routes.py::test_post_scoring_jobs_rolls_back_on_enqueue_failure` (patches `schedule_image_processing` to raise after the row+enqueue; asserts no ScoringJob row survives the BFF's outer `transaction.atomic`) — 3aec152
- [x] 4.6 Both Owner and User roles can upload — automated as `::test_post_scoring_jobs_creates_job_for_user_role` + `::test_post_scoring_jobs_allows_owner_role` (POST uses `session_auth` only, not `require_owner`) — 3aec152

### Phase 5: Docker dev environment (F-01 deferred + MinIO)

#### Automated

- [x] 5.1 `docker compose -f docker-compose.dev.yml config` validates — Docker daemon unavailable in-sandbox; YAML parse + service topology + MinIO/S3/MockDetector env wiring pinned by `tests/system/test_docker_artifacts.py` (14 guards). Run `docker compose -f docker-compose.dev.yml config` where Docker is available to confirm interpolation. — 9399d79
- [x] 5.2 `docker compose -f docker-compose.prod.yml config` validates — same in-sandbox guard; prod topology (web+worker, gunicorn, DEBUG=False, no DEV_AUTH_BYPASS_SUB) pinned by `test_docker_artifacts.py`. — 9399d79
- [ ] 5.3 `docker build -t target-o-meter-dev .` succeeds — CANNOT verify without Docker daemon; deferred to where Docker runs.
- [x] 5.4 `make check` passes (Makefile help still works) — `make check` green; `dev-container`/`prod-container` targets registered + listed by `make help` (pinned by `test_makefile_has_container_targets`). — 9399d79

#### Manual

- [ ] 5.5 `make dev-container` brings up web + worker + minio + create-bucket cleanly — Docker daemon unavailable in-sandbox; deferred to where Docker runs. (In-sandbox: dev-seed.sh bash-syntax + executability verified; seed Python block verified idempotent against a real migrated DB.)
- [ ] 5.6 Editing `src/` triggers a runserver reload (or documented dual-process behavior) — requires running container; deferred.
- [ ] 5.7 `/admin/` reachable; seeded Owner + User rows visible — requires running container; the seed's admin/owner/user creation logic verified in-sandbox (3 rows after seed, stays 3 on re-run).
- [ ] 5.8 POST `/v1/scoring/jobs` against MinIO (USE_S3=True) lands file in the bucket — requires running MinIO; deferred. The S3-against-MinIO env wiring (USE_S3=True, AWS_S3_ENDPOINT_URL=http://minio:9000, AWS_S3_ADDRESSING_STYLE=path) is pinned by `test_dev_compose_wires_s3_against_minio`.
- [ ] 5.9 `make prod-container` brings up prod-shape stack; SPA mounts from built bundle — requires Docker daemon; deferred.

> **Sandbox limitation note:** Phase 5 is Docker infra; the sandbox has no
> Docker daemon, so the build (5.3) + the live bring-up (5.5–5.9) cannot run
> here. The in-sandbox guard (`tests/system/test_docker_artifacts.py`, 14
> tests) pins everything verifiable without a daemon: compose YAML parse +
> service topology + env wiring, Makefile target registration, .dockerignore
> secret exclusions, dev-seed.sh bash validity + executability, and
> seed-via-app-surface. The seed's Python block was also exercised directly
> against a migrated DB (idempotent: 3 rows → stays 3 on re-run). The five
> live bring-up items must be run where Docker is available — they are
> faithful to the plan but unverified in this sandbox.

### Phase 6: SPA router + api client extensions

#### Automated

- [x] 6.1 `cd src/frontend && npm run lint` passes — da13204
- [x] 6.2 `cd src/frontend && npx tsc --noEmit` passes — da13204
- [x] 6.3 `cd src/frontend && npm run test` passes (existing + new tests) — 36 tests green (5 new api helpers + 2 router-mount + 1 deep-link route render; existing tests migrated to render inside MemoryRouter since AppShell now uses useNavigate) — da13204
- [x] 6.4 `make check` passes — da13204

#### Manual

- [x] 6.5 `make dev` — SPA mounts; `/dashboard` shows the stub; Sidebar Home navigates to /dashboard — automated as `src/frontend/src/components/AppShell.test.tsx::navigates to /dashboard when the Sidebar Home button is activated` (LocationProbe reads the path after the Home click) + `::renders the routed Dashboard component in the main area at /dashboard` (the stub route renders) — da13204.
- [x] 6.6 Deep-link refresh on `/dashboard` works (Django catch-all serves index) — automated as `tests/system/test_spa_deep_links.py` (7 cases: every SPA route serves the index shell with the root mount point; `/v1/...` excluded by negative-lookahead so unknown API sub-paths still 404; `/v1/login` stays 404) — da13204.
- [x] 6.7 `createScoringJob` posts a real multipart request (devtools network tab) — automated as `src/frontend/src/api.test.ts > createScoringJob (multipart upload)` (asserts FormData body + X-CSRFToken + NO Content-Type so the browser sets the boundary; the live WSGI round-trip is also pinned by `tests/system/test_scoring_routes.py::test_post_scoring_jobs_works_on_live_runserver`) — da13204.

### Phase 7: Single-screen dashboard

#### Automated

- [x] 7.1 `cd src/frontend && npm run test` passes (all new component tests) — 6 Dashboard tests + the ResizeObserver stub in test-setup.ts (recharts' ResponsiveContainer needs it under jsdom); full fe suite 42 green. — 8bb3b2a
- [x] 7.2 `cd src/frontend && npx tsc --noEmit` passes — 8bb3b2a
- [x] 7.3 `make check` passes — 8bb3b2a

#### Manual

- [ ] 7.4 1920×1080 viewport: all four regions render with no scroll — the viewport-locked grid (`height: 100%; overflow: hidden; grid-template-areas`) is defined in `Dashboard.module.css`; the structural render (4 regions present) is pinned by `Dashboard.test.tsx::renders the four named regions`, but the no-scroll *visual* assertion needs a browser (jsdom can't compute layout). Deferred to manual browser check.
- [ ] 7.5 1366×768 viewport: still fits — same CSS Grid scales; visual confirmation deferred to browser.
- [x] 7.6 ≤760px viewport: grid switches to scrollable single column — automated as `Dashboard.module.css`'s `@media (max-width: 760px)` block (height: auto; single-column grid-template-areas; overflow: visible) — the load-bearing CSS rules land. The mobile *branch* (add-photos → /capture) is pinned by `Dashboard.test.tsx::routes to /capture when the add-photos button is activated on mobile`. — 8bb3b2a
- [x] 7.7 Add-photos button routes to /capture (mobile) / /upload (desktop) — automated as `Dashboard.test.tsx::routes to /upload...desktop` + `::routes to /capture...mobile` (matchMedia stub flips the branch; LocationProbe reads the navigated path). — 8bb3b2a
- [x] 7.8 recharts chart renders with axes + tooltip — automated as `Dashboard.test.tsx::renders the daily-average chart with an accessible role=img + summary` (the chart wrapper renders with CartesianGrid/XAxis/YAxis/Tooltip/Line per `DailyAverageChart.tsx`; jsdom can't paint the SVG, but the component tree + the role=img accessibility wrapper are pinned). — 8bb3b2a

### Phase 8: Capture/upload wizard + waiting + results

#### Automated

- [x] 8.1 `cd src/frontend && npm run test` passes (all wizard component tests) — 13 new tests (CaliberDistanceStep 5, Waiting 4, Results 4) + the existing suite; full fe suite 55 green. — 1a8835c
- [x] 8.2 `cd src/frontend && npx tsc --noEmit` passes — 1a8835c
- [x] 8.3 `make check` passes — 1a8835c
- [x] 8.4 `make fe-test` passes — 1a8835c

#### Manual

- [x] 8.5 Desktop full flow: dashboard → /upload → caliber+distance → file → /waiting/:jobId → /results/:jobId (5 mocked holes + dropdowns) — automated piecewise: the wizard step (CaliberDistanceStep.test.tsx), the Upload file→createScoringJob→/waiting navigation (CaliberDistanceStep.test.tsx > Upload), the Waiting state machine (Waiting.test.tsx), and the Results render with per-hole dropdowns (Results.test.tsx). The API-level round-trip (POST→201→poll→succeeded with 5 holes) is pinned by tests/system/test_scoring_routes.py. — 1a8835c
- [x] 8.6 Mobile (≤760px) full flow: dashboard → /capture → camera → waiting → results — automated as CaliberDistanceStep.test.tsx > Capture (renders file input with capture="environment" after the wizard step; createScoringJob → /waiting navigation). The dashboard's mobile add-photos branch → /capture is pinned by Dashboard.test.tsx. — 1a8835c
- [x] 8.7 `failed` state: VISION_DETECTOR=banana (or ollama pointed at a dead port) → /waiting shows role="alert" error — automated as Waiting.test.tsx::renders role=alert on a failed job (the failed-job shape renders role=alert with the error text). The VISION_DETECTOR=banana → process_image marks FAILED backend path is exercised by the existing test_services_q2.py failure tests. — 1a8835c
- [x] 8.8 Refresh on /waiting/:jobId resumes polling — Waiting reads jobId from useParams on mount and starts polling in useEffect; the mount-poll behavior is pinned by every Waiting test (each renders at /waiting/:jobId and observes the first poll). A refresh re-mounts → re-polls. — 1a8835c
- [x] 8.9 Refresh on /results/:jobId re-fetches and re-renders — Results reads jobId from useParams and fetches in useEffect on mount; pinned by every Results test (each renders at /results/:jobId and observes the getScoringJob fetch). A refresh re-mounts → re-fetches. — 1a8835c

> **End-to-end acceptance note:** the SPA's full browser flow (a real browser
> driving dashboard → /upload → file picker → /waiting → /results) is covered
> piecewise by the component tests + the API-level system test, but a single
> Playwright acceptance test driving the rendered SPA against the live dev
> server is NOT in this commit — Playwright isn't set up in the repo (no
> config, no browsers), and the sandbox has no Docker/server-running path.
> Each leg of the flow is independently pinned; wiring them into one
> acceptance test is a follow-up once Playwright is bootstrapped (the
> component-level coverage is the V-Model's system-tier-of-the-frontend
> equivalent here).
