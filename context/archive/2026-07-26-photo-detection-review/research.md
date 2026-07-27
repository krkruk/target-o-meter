---
date: 2026-07-26T15:29:43+02:00
researcher: ZCode (via /10x-research)
git_commit: 42a8c5e7f74bafd0148928841ae7e3f09ddfdaf4
branch: master
repository: krkruk/target-o-meter
topic: "S-02 photo-detection-review — vision integration surface, BFF scoring contract, SPA dashboard/wizard skeleton, S3/MinIO storage swap, and the scope contradictions between the user brief and existing foundation artifacts"
tags: [research, codebase, vision, bff, frontend, spa, storage, s3, minio, django-storages, django-q2, django-ninja, react, vite, ddd]
status: complete
last_updated: 2026-07-26
last_updated_by: ZCode (via /10x-research)
---

# Research: S-02 photo-detection-review

**Date**: 2026-07-26T15:29:43+02:00
**Researcher**: ZCode (via /10x-research)
**Git Commit**: 42a8c5e7f74bafd0148928841ae7e3f09ddfdaf4
**Branch**: master
**Repository**: krkruk/target-o-meter

## Research Question

How do we structure the S-02 (`photo-detection-review`) change given the user's directive brief:

> Integrate the vision domain. Use the existing module interfaces to expose cross-domain API for photo upload. Extend the initial assumptions: Railway **does** offer S3 buckets, so `FileSystemStorage` is the optional on-prem/debug mode and the PROD-ready implementation relies on MinIO S3. Create docker-compose for Django + dev-ready MinIO S3. Build the first frontend (collapsible left menu + primary dashboard: hero stats, add-photos button that branches PC-picker vs mobile capture/upload, a caliber→distance→camera wizard with a waiting screen and a results list with per-hole correction dropdowns, and a daily-average chart for the past month — all in one screen, no scroll). Calibers: `.22LR / 9x19mm / .223Rem / .32ACP / 7.62x39 / Slug`; distances: `7m / 15m / 25m / 50m / 100m / 200m / 300m / 500m`. **Mock the data for now** — integrate with the vision module in a future change.

## Summary

The codebase is **much further along than the roadmap's "boundary" framing suggests**, and the user's brief **contradicts three foundation artifacts**. After four scoping questions the user has confirmed direction; the contradictions become explicit Open Questions for `/10x-plan` and a follow-up foundation update rather than blockers. The recommended S-02 scope is **UI skeleton + BFF contract with mocked responses** (the user's pick), and S-03 wires the real vision pipeline.

Three load-bearing findings shape everything below:

1. **The vision seam is already built and callable.** `src/domains/vision/services.py:69` (`schedule_image_processing`) and `:238` (`get_job`) are the exact functions the BFF orchestrates. The DTOs at `dtos.py:18-51` are the wire contract. S-02's BFF can be written today against the real seam and **only the response payload needs mocking** (return a fake `ScoringResultDTO` instead of awaiting q2). This collapses "mocked BFF" into "real BFF with a mocked detector" — see §6.

2. **`ScoringStorage` hardcodes `FileSystemStorage`** at `pipeline/storage.py:14,32`. The S3 swap requires (a) adding `django-storages` + `boto3` deps, (b) an env-driven `STORAGES["default"]` swap in `settings.py:332-339`, and (c) switching `ScoringStorage.__init__` to `django.core.files.storage.default_storage`. **The filesystem-shaped methods (`absolute_path`, `write_deliverable`, `_safe_join`) break under S3** and must be refactored — that refactor belongs in S-03 (when the q2 task body actually round-trips bytes through storage), not S-02.

3. **The user's caliber/distance lists are NOT ISSF** and conflict with `vision/ports.py:21` (`TargetType = Literal["air_pistol", "precision_pistol"]`) and AGENTS.md §2 (10m Air Pistol / 25m-50m Precision Pistol only). The user has decided to use the lists as-is. This forces a vision-domain extension (or a separate UI-only taxonomy mapped onto ISSF types internally) — flagged as the highest-impact Open Question because it touches the PRD's §2 Domain Constraints.

Two artifacts the user's brief contradicts and that must be reconciled by `/10x-plan` or a `/10x-tech-stack-selector` re-run:

- **`context/foundation/infrastructure.md`** (2026-05-26) lists "No managed object storage" as a Railway weakness (Devil's Advocate weakness #3) and the Pre-Mortem narrative says "ephemeral filesystem forced an S3 integration." The user now asserts Railway offers S3 buckets. **The foundation doc must be updated** if the user is right; this research treats the user's updated assumption as authoritative (per their answer to scoping Q2).
- **`AGENTS.md` §1** pins `FileSystemStorage` + hashed-path bucketing. The user wants S3-primary with FS fallback. **AGENTS.md §1 must be updated** as part of this change or a paired foundation change.

## Detailed Findings

### 1. Vision integration surface — what the BFF actually calls

The BFF will orchestrate three vision services. All are keyword-only, all are documented, all are tested (`src/domains/vision/tests/test_services_q2.py`).

**Enqueue** — `schedule_image_processing(*, user_uuid, input_path, target_type="air_pistol", caliber_hint=None) -> str` at `src/domains/vision/services.py:69-96`.
- Returns `str(job.id)` (UUID stringified at `services.py:96`) — the cross-domain safe key per AGENTS.md §5.
- Wraps `ScoringJob.objects.create(...)` + `async_task("src.domains.vision.services.process_image", str(job.id))` in `transaction.atomic()` (`services.py:81-95`). The q2 broker is the same SQLite DB (`settings.Q_CLUSTER['orm']='default'` at `settings.py:278`), so the enqueue participates in the surrounding transaction — if anything after it in the BFF's outer atomic fails, the q2 task row rolls back too. **This is the load-bearing reason AGENTS.md §6.2 mandates the BFF-level `transaction.atomic` wrap.**
- `input_path` is a stored path relative to the `ScoringStorage` root, as produced by `ScoringStorage.save_upload(bytes, original_name)` (`pipeline/storage.py:56-68`). The BFF must call `save_upload` first, then pass the returned relative path here.

**Poll** — `get_job(job_id, user_uuid) -> ScoringJobDTO` at `src/domains/vision/services.py:238-256`.
- Owner-only: raises `PermissionError` if `job.user_uuid != user_uuid` AND on `DoesNotExist` (`services.py:249-254`) so an ID-prober can't distinguish "exists, not mine" from "doesn't exist." **BFF maps both to 404.**
- Returns `ScoringJobDTO` rebuilt from a raw `JSONField` dict at read time (`_job_to_dto` at `services.py:278-322`). The rebuild requires `result_dict["ok"]` truthy and each hole to have `{"x","y","score"}` (`services.py:295`) — otherwise it raises `ValueError`. **The DTO-to-JSON-to-DTO round-trip is known-fragile** (the docstring at `services.py:282-287` flags it). The BFF must treat `result` as nullable even on `succeeded` jobs.

**Reap stuck jobs** — `reap_stuck_jobs(timeout_seconds=1200) -> int` at `services.py:204-235`. Flips stale `RUNNING` rows (older than `STUCK_RUNNING_TIMEOUT_SECONDS=2×Q_CLUSTER['retry']` at `services.py:201`) to `FAILED`. **Required for the PRD §Guardrail "no dead-end states"** (`prd.md:37,114`). Intended to be called by a scheduled q2 task or BFF-on-GET. **S-02 should wire this into the poll endpoint** (call `reap_stuck_jobs()` before `get_job()`) so the waiting screen always resolves.

**DTO contract** (`src/domains/vision/dtos.py:18-51`):

| DTO | Fields |
|---|---|
| `DetectedHoleDTO` (`:18`) | `x:int` `y:int` `score:int` `confidence:float` `caliber:Optional[str]` — 1024×1024 frame |
| `ScoringResultDTO` (`:28`) | `holes:list[DetectedHoleDTO]` `target_type:TargetType` `notes:Optional[str]` `detector_name:str` |
| `ScoringJobDTO` (`:37`) | `job_id:UUID` `status:str` `target_type:TargetType` `caliber_hint:Optional[str]` `result:Optional[ScoringResultDTO]` `error:Optional[str]` `created_at:Optional[str]` `completed_at:Optional[str]` |

**Status values** (`src/domains/vision/models.py:24-28`): `queued | running | succeeded | failed` (lowercase strings). The DTO field is typed plain `str` (`dtos.py:46`), not the enum — the BFF wire contract should declare a string union.

**Identity plumbing is already in place.** `get_user_context(sub) -> UserContextDTO` at `src/domains/identity/services.py:85-93` returns `user_uuid: UUID` on the DTO (`identity/dtos.py:27`). The existing `require_owner(request)` helper at `src/bff/api.py:45-64` returns this DTO. **Important RBAC note**: the upload endpoint does NOT need `require_owner` — both Owner and User roles can upload (`prd.md:122-129`; F-01 research.md:294 maps "Upload / photograph a target" to "Allow | Allow"). `session_auth` (401 anon) is sufficient; `get_job` enforces per-job ownership.

### 2. The four contradictions between the user's brief and the codebase

| # | Brief says | Codebase/foundation says | Resolution (user-confirmed) |
|---|---|---|---|
| 1 | "the very first front-end related task" | S-01 shipped a full React+Vite SPA: `App.tsx`, `AppShell`, `Sidebar`, `TopBar`, `NickPrompt`, `Welcome`, `api.ts` | **Extend the S-01 SPA.** React Router lands here (the S-01 code already comments it — `App.tsx:6`). |
| 2 | "Railway DOES offer S3 buckets" | `infrastructure.md:46` lists "No managed object storage" as a Railway weakness | **Treat S3 as the new prod target.** FS stays as debug fallback. `infrastructure.md` + `AGENTS.md §1` need updating. |
| 3 | Calibers `.22LR / 9x19mm / .223Rem / .32ACP / 7.62x39 / Slug`; distances `7m / 15m / 25m / 50m / 100m / 200m / 300m / 500m` | `vision/ports.py:21` knows only `air_pistol` \| `precision_pistol`; AGENTS.md §2 is ISSF-only (10m / 25m-50m) | **Use the user's lists as-is.** Forces a vision-domain extension — Open Question #1. |
| 4 | "we will simply mock the data for now" + brief describes full wizard/dashboard/chart | Roadmap S-02 outcome (roadmap.md:120): capture+upload+detect+review, **no persistence** | **UI skeleton + BFF contract, mocked responses.** Real vision wiring is S-03. |

### 3. Caliber taxonomy — vision's current state vs the user's list

Vision has **two parallel sources of truth that already disagree slightly**. Neither matches the user's list cleanly.

**Diameter table** (`src/domains/vision/pipeline/caliber_taxonomy.py:25-32`):
```python
CALIBER_DIAMETER_MM = {"22lr": 5.7, ".223rem": 5.56, "9mm": 9.01,
                       ".45acp": 11.5, "7.62x39": 7.9, "12-gauge": 18.0}
DEFAULT_DIAMETER_MM = 9.0
```
Aliases (`:16-19`): `9x19 → 9mm`, `slug → 12-gauge`. Normalization is lowercase + alias-resolved (`:40-50`) — but only `diameter_mm` lowercases (`:58`), creating a **split-brain bug** (see below).

**LLM prompt canonical forms** (`src/domains/vision/detectors/prompt.py:15`):
```python
_CANONICAL_CALIBERS = ["22lr", ".223Rem", "9mm", ".45ACP", "7.62x39", "12-gauge"]
```

**Mismatch table vs the user's list:**

| User's entry | Vision status | Problem |
|---|---|---|
| `.22LR` | ⚠️ Partial | Table has `22lr` (no dot). `normalize` lowercases but doesn't strip the leading dot. `".22lr"` is NOT in the table → silent `DEFAULT_DIAMETER_MM=9.0`. |
| `9x19mm` | ⚠️ Partial | Alias covers `9x19` but NOT `9x19mm` (the `mm` suffix). `.lower()` → `9x19mm`, not in table → DEFAULT. |
| `.223Rem` | ✅ Match | Both vision sources handle it. |
| `.32ACP` | ❌ Missing | Vision has `.45acp` but **no `.32acp`**. Falls to DEFAULT. The LLM may emit it free-text but no diameter is known. |
| `7.62x39` | ✅ Match | Identical in both vision sources. |
| `Slug` | ⚠️ Indirect + buggy | Alias `slug → 12-gauge` exists in `normalize`, but `diameter_mm` re-lowercases and looks up the **diameter table** (which has `12-gauge`, not `slug`) → the alias is lost → silent DEFAULT. **Real bug.** |

**Implications for S-02:**
- The BFF mock can accept any string (the seam is free-text, `caliber_hint: Optional[str]`, max 64 chars — `models.py:40`).
- The vision domain needs taxonomy work **before S-03's real wiring** can correctly score `.32ACP` and normalize the user's spellings. This is Open Question #1's concrete shape.
- The `normalize`/`diameter_mm` split-brain bug should be fixed in S-03 (or a paired vision-domain change), not papered over in the BFF.

### 4. Distance is not a vision concept — orthogonal dimension

Vision's `TargetType` (`ports.py:21`) selects the **printed target face** (10-ring geometry, same 1024×1024 frame for both). Distance is a **range parameter the user picks**; vision does not model it. The two are orthogonal:

- `target_type` = which ISSF target face was shot at (geometry/layout).
- distance = how far away the shooter was (affects nothing in the current pipeline).

**Vision today has no `distance` field.** The user's distance list (7m/15m/25m/50m/100m/200m/300m/500m) must be modeled somewhere. Three options for `/10x-plan`:
1. Add a `distance` column to `ScoringJob` (cleanest, requires a migration).
2. Encode distance in `caliber_hint` (hacky, overloads the field).
3. Carry distance in a new BFF-owned DTO/metadata table (keeps vision pure, BFF stores the param).

The PRD's FR-009 ("form to confirm caliber, distance, and weapon type") is **S-03 scope** (roadmap.md:37), so S-02 only needs to *collect* distance in the wizard and either pass it through or store it as a BFF-level mock. **Recommended: option 3** (BFF-level mock field) so S-02 doesn't touch vision's model and S-03 can promote it cleanly.

### 5. SPA scaffold from S-01 — what S-02 extends

**Stack confirmed** (`src/frontend/package.json:15-30`): React `^18.3.1`, Vite `^5.3.3`, Vitest `^1.6.0`, `@testing-library/react`, jsdom. **No router, no chart lib, no UI lib, no state lib** (grep across `package-lock.json` confirmed zero transitive presence of react-router/recharts/MUI/Tailwind/shadcn/zustand/tanstack).

**CSS Modules convention**: `Component.module.css` co-located, default-imported (`import styles from './X.module.css'`), camelCase class keys, state exposed via `data-*` attributes (not classes) — pinned by the AppShell collapsed-state regression test (`AppShell.test.tsx:56-70`). Design tokens live once in `src/frontend/src/styles.css:16-24` as `:root` custom properties (`--color-bg`, `--color-primary`, `--sidebar-width`, etc.). The header comment at `styles.css:7` explicitly says "No Tailwind, no CSS-in-JS."

**Auth seam** (`App.tsx:15-57`): single `useState<Me|null>`, `useEffect` calls `getMe()` on mount, 401 maps to `{authenticated:false, user:null}` (`api.ts:42`). `me===null` → loading; `!me.authenticated` → `<Welcome>`; else `<AppShell>` + optional `<NickPrompt>` overlay. **No client-side router** — confirmed by the comment at `App.tsx:6`: *"React Router lands in S-02/S-03."*

**CSRF pattern** (`api.ts:28-38`): `readCsrfToken()` parses `document.cookie` for `csrftoken=` (works because `CSRF_COOKIE_HTTPONLY=False` at `settings.py:233`). `jsonHeaders()` sets `Content-Type: application/json` + `X-CSRFToken`. **For multipart upload, do NOT reuse `jsonHeaders()`** — the browser must set the `boundary`. Write a new helper that sets only `X-CSRFToken` and pass `body: new FormData(...)`.

**Testing patterns**: component tests mock the `api` module (`vi.spyOn(api, 'getMe').mockResolvedValue(...)`); the `api.ts` contract test mocks `globalThis.fetch` directly (`api.test.ts:35-73`). `beforeEach(() => vi.restoreAllMocks())` is the norm. jsdom location is read-only; the existing escape hatch at `api.test.ts:18-33` (delete + redefine `window.location`) is the model if S-02 needs to test navigation.

**Accessibility is load-bearing** — tests assert on roles + aria-labels: `role="navigation" aria-label="Main navigation"` (`Sidebar.tsx:19-21`), `role="dialog"` for overlays (`NickPrompt.tsx:46`), `role="alert"` for errors (`NickPrompt.tsx:63`), `role="status"` for loading (`App.tsx:28`). New components must follow this.

**Mobile breakpoint convention**: `Welcome.module.css:87-92` uses `@media (max-width: 760px)` — that 760px is the de-facto mobile threshold to reuse for the PC-picker vs mobile-capture/upload button split.

**What S-02 must add**:
- `react-router-dom` (the S-01 comment promised it; needed for `/dashboard`, `/capture`, `/upload`, `/waiting/:jobId`, `/results/:jobId` routes).
- A chart library (recharts is the most common React-native fit; visx is lower-level). Only one chart is needed (daily average past month), so even a hand-rolled SVG would be defensible — but a lib is faster.
- Mobile camera capture: native `<input type="file" accept="image/*" capture="environment">` — no library needed.
- A multipart upload helper in `api.ts`.
- Mocked data fixtures (a `src/mocks/` module or per-test `vi.spyOn` — the latter matches house style).

**Single-screen-no-scroll constraint**: the current shell uses `min-height: 100vh` + `overflow: auto` on main (`AppShell.module.css:3,18`). The dashboard must replace the placeholder (`AppShell.tsx:32-36`) with a viewport-sized layout — likely `height: 100vh` + flexbox + `overflow: hidden` on the dashboard grid. **This is the hardest layout problem in S-02** and should get its own plan phase.

### 6. The "mocked BFF" decision collapses into "real BFF + mocked detector"

The user picked "UI skeleton + BFF contract with mocked responses." Reading the vision seam closely, **the cleanest implementation is not to mock the BFF — it's to mock the detector**. The vision domain already ships `MockDetector` (`src/domains/vision/detectors/mock_detector.py`, used by `tests/test_mock_detector.py` and the CLI quick-start in the vision README). The factory at `detectors/factory.py` already builds it by name.

**Two viable shapes for S-02's BFF:**

| Shape | What ships | Pros | Cons |
|---|---|---|---|
| **A. Real BFF, mocked detector** | Real `POST /v1/scoring/jobs` → real `schedule_image_processing` → q2 → `process_image` runs `MockDetector` → real `get_job` poll. | S-03 becomes a one-line detector swap (factory env switch). The whole upload→poll→result round-trip is exercised end-to-end against real storage and real q2. The waiting screen, status transitions, and stuck-job reaping are all real. | Requires the storage layer to work end-to-end (so the S3/MinIO swap must land in S-02, not S-03). Slower to ship. |
| **B. Mocked BFF endpoints** | `POST /v1/scoring/jobs` returns a fake `job_id` immediately; `GET /v1/scoring/jobs/{id}` returns canned fixtures. No q2, no storage. | Fastest to ship. Storage migration can be S-03. | S-03 has to write the real BFF *and* swap the mock — twice the work. The waiting-screen UX is faked with `setTimeout`. |

**Recommendation: Shape A.** It honors the user's "use the existing module interfaces to expose cross-domain API for photo upload" instruction literally, and it makes S-03 a detector-config swap rather than a BFF rewrite. The cost is that the storage swap must land in S-02 — but the storage swap is needed for the docker-compose deliverable anyway, so the work overlaps. `/10x-plan` should make this choice explicit and let the user override.

### 7. Storage migration — FileSystemStorage → S3 (MinIO local / Railway S3 prod)

**Current state**:
- `STORAGES["default"] = FileSystemStorage` (`settings.py:332-339`).
- `MEDIA_ROOT` is NOT set — `ScoringStorage.__init__` falls back to `BASE_DIR/"scoring_storage"` (`pipeline/storage.py:28-31`).
- `ScoringStorage` **hardcodes** `from django.core.files.storage import FileSystemStorage` (`pipeline/storage.py:14`) and instantiates it directly (`:32`). It does NOT consult `STORAGES["default"]`. Swapping settings alone has no effect on uploads.
- `django-storages`, `boto3`, `minio` are **not in `pyproject.toml`** (grep confirmed).

**The swap (three layers)**:

1. **Dependencies** (`pyproject.toml`): add `django-storages>=1.14.4` + `boto3>=1.35` to runtime deps. The Python `minio` package is NOT needed — django-storages talks to MinIO via the S3 protocol through boto3, driven by `AWS_S3_ENDPOINT_URL`.

2. **Settings** (`settings.py:332-339`): env-driven backend swap using the existing `_env_bool` helper (`settings.py:51-53`):
   ```python
   USE_S3 = _env_bool("USE_S3", False)
   _default_backend = ("storages.backends.s3.S3Storage" if USE_S3
                       else "django.core.files.storage.FileSystemStorage")
   STORAGES = {"default": {"BACKEND": _default_backend},
               "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
   if USE_S3:
       AWS_ACCESS_KEY_ID = os.environ["AWS_ACCESS_KEY_ID"]
       AWS_SECRET_ACCESS_KEY = os.environ["AWS_SECRET_ACCESS_KEY"]
       AWS_STORAGE_BUCKET_NAME = os.environ["AWS_STORAGE_BUCKET_NAME"]
       AWS_S3_ENDPOINT_URL = os.environ.get("AWS_S3_ENDPOINT_URL")  # MinIO only
       AWS_S3_ADDRESSING_STYLE = os.environ.get("AWS_S3_ADDRESSING_STYLE", "auto")  # "path" for MinIO
       AWS_S3_FILE_OVERWRITE = False
       AWS_DEFAULT_ACL = None  # S3 disables ACLs by default 2024+
   ```
   **Canonical backend class for Django 6.0 in 2026: `storages.backends.s3.S3Storage`** (not the deprecated `S3Boto3Storage`). `S3Boto3Storage` was deprecated in django-storages 1.14 and slated for removal. Cited: [django-storages Amazon S3 docs](https://django-storages.readthedocs.io/en/latest/backends/amazon-S3.html), [django-storages CHANGELOG](https://github.com/jschneier/django-storages/blob/master/CHANGELOG.rst).

3. **`ScoringStorage.__init__`** (`pipeline/storage.py:25-32`): switch to `django.core.files.storage.default_storage` when no explicit `location`/`base_url` is passed (so the env swap flows through). Keep the explicit-`location` path for tests/CLI.

**What does NOT cleanly swap** (the path-shaped methods):
- `deliverable_dir()` returns a `Path` and `process_image` calls `.mkdir(parents=True, exist_ok=True)` on it (`services.py:142-143`). S3 has no directories.
- `absolute_path()` returns a `Path` (`storage.py:87-89`) fed to `PipelineRunner.run(input_abspath, ...)` (`services.py:137-138`). **OpenCV fundamentally needs local bytes** — `cv2.imread` cannot read an S3 key. An S3 backend must download to a tempfile before the pipeline runs and upload deliverables back after.
- `_safe_join` uses filesystem `resolve()` + `relative_to()` (`storage.py:38-54`). S3 keys need a prefix-based containment check instead.

**Recommended split**: S-02 lands layers 1+2+3 (deps + settings + `__init__` switch). **The full S3-compatible read/write refactor of the pipeline (tempfile download, prefix-based paths) is S-03 scope**, when the q2 task body actually round-trips bytes. S-02 with `MockDetector` can run end-to-end against `FileSystemStorage` (FS fallback) even after the settings swap, because `USE_S3=False` in the default dev path. `/10x-plan` must make this split explicit so the refactor isn't scoped out by accident.

### 8. docker-compose — Django + MinIO for local S3-compatible dev

Canonical modern MinIO uses `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` (the old `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` are deprecated), `--console-address ":9001"` is effectively mandatory, and an `mc` sidecar creates the bucket idempotently. Reference shape (the plan will refine):

```yaml
services:
  django:
    build: .
    depends_on: { minio: { condition: service_healthy } }
    environment:
      DEBUG: "True"
      USE_S3: "True"
      AWS_ACCESS_KEY_ID: minioadmin
      AWS_SECRET_ACCESS_KEY: minioadmin
      AWS_STORAGE_BUCKET_NAME: target-o-meter-local
      AWS_S3_ENDPOINT_URL: http://minio:9000
      AWS_S3_ADDRESSING_STYLE: path
    ports: ["8000:8000"]
    volumes: ["./src:/app/src"]
    command: ["./src/manage.py", "runserver", "0.0.0.0:8000"]  # + qcluster sidecar

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    ports: ["9000:9000", "9001:9001"]
    environment: { MINIO_ROOT_USER: minioadmin, MINIO_ROOT_PASSWORD: minioadmin }
    volumes: ["minio-data:/data"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 30s
      timeout: 20s
      retries: 3

  create-bucket:
    image: minio/mc:latest
    depends_on: { minio: { condition: service_healthy } }
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio:9000 minioadmin minioadmin &&
      mc mb --ignore-existing local/target-o-meter-local &&
      exit 0"

volumes:
  minio-data:
```

**Wiring notes**:
- Django inside the compose network talks to MinIO at `http://minio:9000`. A host browser fetching a deliverable uses `http://localhost:9000`. If deliverable URLs are surfaced to the SPA (S-03 concern), set `AWS_S3_CUSTOM_DOMAIN=localhost:9000` for local dev.
- `AWS_S3_ADDRESSING_STYLE=path` is required for MinIO; real AWS leaves it `auto`.
- The q2 worker needs its own container (or a second process in the django container) — the CV pipeline runs in the worker, not the web process. AGENTS.md §2 caps at 3 workers.
- Cited: [minio/minio Docker Hub](https://hub.docker.com/r/minio/minio), [MinIO + django-storages](https://naomiaro.hashnode.dev/using-minio-with-django-storages), [Local S3 with Django + MinIO](https://tomwojcik.com/posts/2020-09-18/local-s3-with-django-and-minio/).

### 9. django-ninja file upload — the BFF route shape

The documented pattern (`File[UploadedFile]` + `Form[Schema]`) fits S-02's needs exactly — file + `target_type` + `caliber_hint` + `distance` alongside the image. Reference shape:

```python
from ninja import Router, Form, File
from ninja.files import UploadedFile
from django.db import transaction
from src.bff.api import session_auth
from src.domains.identity.services import get_user_context
from src.domains.vision.pipeline.storage import ScoringStorage
from src.domains.vision.services import schedule_image_processing, get_job, reap_stuck_jobs

router = Router()

class ScoringJobIn(Schema):
    target_type: str = "air_pistol"
    caliber_hint: str | None = None
    distance_m: int | None = None   # BFF-level mock field (vision has no distance concept)

@router.post("/scoring/jobs", auth=session_auth, response={201: ScoringJobOut})
@transaction.atomic   # AGENTS.md §6.2
def create_scoring_job(request, details: Form[ScoringJobIn], file: File[UploadedFile]) -> ScoringJobOut:
    user_dto = get_user_context(str(request.user.sub))
    storage = ScoringStorage()
    input_path = storage.save_upload(file.read(), file.name)
    job_id = schedule_image_processing(
        user_uuid=user_dto.user_uuid,
        input_path=input_path,
        target_type=details.target_type,
        caliber_hint=details.caliber_hint,
    )
    return ScoringJobOut(job_id=job_id, status="queued")

@router.get("/scoring/jobs/{job_id}", auth=session_auth, response={200: ScoringJobDTO})
def get_scoring_job(request, job_id: str) -> ScoringJobDTO:
    user_dto = get_user_context(str(request.user.sub))
    reap_stuck_jobs()   # PRD §Guardrail: no dead-end states
    try:
        return get_job(job_id, user_dto.user_uuid)
    except PermissionError:
        raise HttpError(404, "Not found") from None
```

Cited: [django-ninja File params docs](https://django-ninja.dev/guides/input/file-params/), [Django 6.0 UploadFile docs](https://docs.djangoproject.com/en/6.0/ref/files/uploads/). The router mounts via `api.add_router("/", scoring_router)` at `src/bff/urls.py` (mirroring `:29-30`), becoming `/v1/scoring/jobs` after the `v1/` prefix at `:40`.

## Code References

### Vision domain (the seam S-02 consumes)
- `src/domains/vision/services.py:69-96` — `schedule_image_processing` (enqueue, atomic, kwargs-only, returns `str(job.id)`)
- `src/domains/vision/services.py:99-196` — `process_image` (q2 task body, idempotency guard, NaN sanitization)
- `src/domains/vision/services.py:204-235` — `reap_stuck_jobs` (stuck-RUNNING cleanup, required for PRD §Guardrail)
- `src/domains/vision/services.py:238-256` — `get_job` (owner-only read, raises `PermissionError` → BFF maps to 404)
- `src/domains/vision/services.py:278-322` — `_job_to_dto` (fragile JSON→DTO rebuild, raises `ValueError` on malformed)
- `src/domains/vision/dtos.py:18-51` — `DetectedHoleDTO`, `ScoringResultDTO`, `ScoringJobDTO` (the wire contract)
- `src/domains/vision/ports.py:21` — `TargetType = Literal["air_pistol", "precision_pistol"]` (ISSF-only)
- `src/domains/vision/models.py:24-28` — `Status` enum (`queued|running|succeeded|failed`)
- `src/domains/vision/models.py:31` — `user_uuid = UUIDField(db_index=True)` (NOT a FK — AGENTS.md §5)
- `src/domains/vision/pipeline/storage.py:14,32` — hardcoded `FileSystemStorage` (the S3 swap point)
- `src/domains/vision/pipeline/storage.py:56-89` — `save_upload`, `deliverable_dir`, `absolute_path`, `_safe_join` (path-shaped methods that break under S3)
- `src/domains/vision/pipeline/caliber_taxonomy.py:16-32` — aliases + diameter table (split-brain bug vs `diameter_mm` at `:58`)
- `src/domains/vision/detectors/prompt.py:15` — `_CANONICAL_CALIBERS` (LLM prompt forms)
- `src/domains/vision/detectors/mock_detector.py` — `MockDetector` (the S-02 mock-data mechanism if Shape A is chosen)

### BFF (the orchestration layer S-02 extends)
- `src/bff/api.py:40-42` — `NinjaAPI`, `session_auth`
- `src/bff/api.py:45-64` — `require_owner` (authorization helper — NOT needed for upload, both roles can upload)
- `src/bff/routers/session_routes.py:38-66` — `/v1/me` GET/PATCH (the model for `/v1/scoring/jobs`)
- `src/bff/routers/owner_routes.py:28-37` — owner-gated route pattern
- `src/bff/urls.py:29-31,40` — router mounting + `v1/` prefix
- `src/bff/views.py:18-21` — `index` view serves the SPA shell

### Identity (user_uuid plumbing)
- `src/domains/identity/services.py:85-93` — `get_user_context(sub) -> UserContextDTO` (returns `user_uuid`)
- `src/domains/identity/dtos.py:27` — `user_uuid: UUID` on the DTO

### Settings + storage config
- `src/target_o_meter/settings.py:51-53` — `_env_bool` helper (model for `USE_S3`)
- `src/target_o_meter/settings.py:270-280` — `Q_CLUSTER` (ORM broker on SQLite, 3 workers, retry 1200)
- `src/target_o_meter/settings.py:332-339` — `STORAGES` dict (the swap point)
- `src/target_o_meter/settings.py:231-233` — `CSRF_COOKIE_HTTPONLY=False` (lets SPA read `csrftoken`)

### SPA (the frontend S-02 extends)
- `src/frontend/package.json:15-30` — deps (React 18.3, Vite 5.3, Vitest 1.6; NO router/chart/UI/state lib)
- `src/frontend/src/App.tsx:6,15-57` — auth seam, no router (comment defers it to S-02/S-03)
- `src/frontend/src/api.ts:28-38` — CSRF pattern (`readCsrfToken`, `jsonHeaders` — do NOT reuse for multipart)
- `src/frontend/src/api.ts:40-77` — `getMe`/`patchMe`/`postLogout`/`login`
- `src/frontend/src/components/AppShell.tsx:32-36` — the dashboard placeholder S-02 replaces
- `src/frontend/src/components/Sidebar.tsx:14-48` — collapsible nav, `onHome` not wired (the seam)
- `src/frontend/src/components/NickPrompt.tsx:19-70` — async form state machine + modal overlay pattern
- `src/frontend/src/styles.css:7,16-24` — design tokens, "No Tailwind, no CSS-in-JS"
- `src/frontend/src/components/Welcome.module.css:87-92` — 760px mobile breakpoint (de-facto threshold)

## Architecture Insights

1. **The vision seam is the contract; the detector is the strategy.** S-02's BFF should program against `schedule_image_processing` + `get_job`, never against the detector. This makes "mocked data" a detector-config concern (use `MockDetector`), not a BFF concern. Swapping to `GoogleAIStudioDetector` in S-03 is a one-line factory/env change.

2. **Atomicity is belt-and-suspenders by design.** `schedule_image_processing` wraps its own `transaction.atomic()` (`services.py:81`). AGENTS.md §6.2 mandates the BFF also wrap. The BFF's wrap is the outer transaction; the service's is a nested savepoint. **Neither should be removed** — the service's block makes it safe from non-BFF callers (tests, CLI), the BFF's block satisfies the multi-domain orchestration contract.

3. **`user_uuid` is a UUID, not a FK — load-bearing for domain isolation.** The `.importlinter` independence contract (`AGENTS.md §6.1`) forbids cross-domain ORM imports. `ScoringJob.user_uuid = UUIDField` (`models.py:31`) is how vision references identity without importing it. S-02 must not introduce a FK here.

4. **The q2 broker is SQLite — the enqueue is transactional.** Because `Q_CLUSTER['orm']='default'`, the `async_task(...)` row lives in the same SQLite DB. If the BFF's outer transaction fails after the enqueue, the task row rolls back too. This is why the BFF-level `transaction.atomic` is not redundant.

5. **`ScoringStorage` is a hand-rolled adapter, not a Django Storage subclass.** It wraps `FileSystemStorage` (delegating `.save` at `storage.py:67`) but does not implement the Django Storage protocol and does not consult `STORAGES["default"]`. The S3 swap requires either constructor DI or a switch to `default_storage`.

6. **OpenCV needs local bytes — S3 is not transparent to the pipeline.** Even with django-storages, `cv2.imread` cannot read an S3 key. The q2 task body must download to a tempfile, run the pipeline, upload deliverables back. This refactor is S-03 scope.

7. **Mock-first is explicit house style.** F-02 shipped `MockDetector`; F-01 shipped an empty `GET /api/users`. S-02 continuing this tradition (Shape A above) is consistent and minimizes S-03 rework.

8. **The PRD §Guardrail "no dead-end states" is an S-02 deliverable.** `prd.md:37,114` requires the waiting screen always resolve. `reap_stuck_jobs()` (`services.py:204`) is the backend support; S-02's poll endpoint should call it before `get_job()`.

## Historical Context (from prior changes)

- `context/archive/2026-07-19-cv-service-boundary/plan.md:66,468,649` — F-02 explicitly deferred the BFF orchestration router to "a follow-up change." **S-02 is that follow-up.** The plan literally anticipated `src/bff/routers/vision_routes.py`.
- `context/archive/2026-07-19-cv-service-boundary/research.md:343,361` — F-02 flagged the wedge risk: "S-02 carries the wedge risk ('Which CV approach actually hits ≥90% fidelity')." The LLM-pivot research later landed the detector at 0.638–0.799 mean Jaccard — **still below the PRD's 0.90 bar**. Closing that gap remains an open S-02/S-03 concern. **Note: the user's "mock the data for now" decision explicitly defers confronting this wedge to S-03.**
- `context/archive/2026-07-25-sign-in-empty-dashboard/plan.md:55-57` — S-01 explicitly deferred to S-02: dashboard content, **Redux/Oval** ("lands in S-02 when the capture wizard introduces real client state"), and **React Router** ("introduced when S-02/S-03 add screens").
- `context/archive/2026-07-25-sign-in-empty-dashboard/research.md:196-197` — S-01 mapped the "Add new photos button (PC file picker; mobile Capture + Upload)" and the "Capture/Upload wizard (caliber, distance, capture/upload, waiting screen, results list with correction dropdowns)" to S-02.
- `context/foundation/lessons.md:12-17` — "One class per file, matching filename." **Directly load-bearing for S-02's** new BFF router/service/model files. The carve-out: `ports.py` and `dtos.py` may hold several contracts.
- `context/foundation/lessons.md:5-10` — "Always set `RAILPACK_DJANGO_APP_NAME` to the full WSGI module path." Relevant only if S-02 touches deploy config.
- `context/foundation/infrastructure.md:46,67,96` — Railway "No managed object storage" weakness + Pre-Mortem narrative about "ephemeral filesystem forced an S3 integration" + Risk Register row "Uploaded target images lost on redeploy (ephemeral filesystem)" whose mitigation says "integrate S3 from day one." **The user's updated assumption (Railway offers S3) supersedes the "No managed object storage" finding; `infrastructure.md` needs updating.**
- `context/foundation/infrastructure.md:91` — Risk Register: "OpenCV build failures or slow deploys on Railpack." F-02 research flagged this for re-visit "when S-02 lands." S-02 is when OpenCV first runs in production.
- `context/foundation/roadmap.md:120` — S-02 outcome verbatim: "user can capture a target photo via device camera, upload it, the CV service runs detection, and the user sees the overall score plus the target photo with holes marked — **without yet persisting anything**." The "without yet persisting" phrasing is the single most load-bearing scope constraint.
- `context/foundation/roadmap.md:133-141` — S-03 outcome: confirm parameters + accept/reject + persist + aggregate. S-03 closes the US-01 vertical; "does not carry the wedge risk itself."
- `context/foundation/roadmap.md:157-159` — Open Roadmap Questions #1 (≥90% bar achievable?) and #2 (long-term image storage vs score-only?) — **#2 directly blocks S-02's storage decision.**

## Related Research

- `context/archive/2026-07-19-cv-service-boundary/research.md` — the original vision seam contract (the `(image_path, caliber, target_type)` form, since rewritten to the LLM-detector form S-02 consumes)
- `context/archive/2026-07-19-cv-service-boundary/research-ai-detection.md` — the LLM-detector pivot that changed the seam
- `context/archive/2026-07-19-cv-service-boundary/research-llm-pivot.md` — companion LLM pivot research
- `context/archive/2026-07-24-oauth-roles-scaffold/research.md` — F-01 identity scaffold (the `user_uuid` plumbing S-02 consumes)
- `context/archive/2026-07-25-sign-in-empty-dashboard/research.md` — S-01 SPA scaffold (the React/Vite/CSS-Modules conventions S-02 extends, and the explicit deferrals to S-02)
- `context/foundation/infrastructure.md` — Railway platform decision (the "No managed object storage" finding the user's brief supersedes)

## Open Questions

These need answers from the user (or a `/10x-tech-stack-selector` / `/10x-frame` re-run) before `/10x-plan` can finalize. Each is tagged with Owner / Block per the roadmap convention.

1. **The caliber/distance taxonomy contradicts AGENTS.md §2 and the vision domain.** The user's lists are not ISSF. Three concrete sub-problems: (a) `.32ACP` is entirely missing from `caliber_taxonomy.py`; (b) `.22LR` and `9x19mm` spellings don't normalize correctly (silent `DEFAULT_DIAMETER_MM` fallback); (c) the `normalize`/`diameter_mm` split-brain bug breaks `Slug`. **Does S-02 extend the vision domain's taxonomy (forcing a vision-domain change + migration), or does it ship a UI-only taxonomy mapped onto `air_pistol`/`precision_pistol` internally with caliber as a free-text hint?** — Owner: user. Block: yes (this shapes the BFF DTO and possibly a vision migration).

2. **Distance has no home in the vision domain.** Vision's `TargetType` is the printed face, not the range. The user's 7m–500m list must be stored somewhere. Recommended: BFF-level mock field on the new scoring DTO (option 3 in §4), promoted to a real `ScoringJob.distance` column in S-03 alongside FR-009. **Confirm this is acceptable, or does the user want the column added now?** — Owner: user. Block: no (BFF-level mock works for S-02).

3. **Shape A (real BFF + MockDetector) vs Shape B (mocked BFF endpoints).** §6 recommends Shape A because it makes S-03 a detector swap. Shape A requires the storage swap to land in S-02. Shape B lets storage be S-03. **Which does the user want?** — Owner: user. Block: yes (determines whether the docker-compose + django-storages work is in S-02 or deferred).

4. **`infrastructure.md` and `AGENTS.md §1` must be updated.** Both currently say FileSystemStorage / "No managed object storage." The user's brief supersedes both. **Is this update part of S-02, or a paired `/10x-tech-stack-selector` re-run before S-02's plan?** — Owner: user. Block: no (S-02 can proceed with the user's stated assumption; the doc update is hygiene).

5. **Roadmap Open Question #2 (long-term image storage vs score-only) is unresolved.** `roadmap.md:158` flags it as blocking the storage choice. The user's brief implies storing images (MinIO/S3 for uploads), but the privacy posture per PRD §Guardrails may want score-only. **Does S-02 store uploaded images long-term, or treat them as ephemeral processing inputs?** — Owner: user. Block: yes for prod posture, no for S-02 mock scope.

6. **The ≥90% fidelity wedge (roadmap.md:157).** F-02 landed the LLM detector at 0.638–0.799 mean Jaccard, below the PRD bar. The user's "mock the data for now" decision defers this to S-03. **Confirm: S-02 ships mocked results and does NOT attempt to close the fidelity gap; S-03 confronts the wedge.** — Owner: user. Block: no (decision already implicit in the user's brief).

7. **Railway S3 verification.** The user asserts Railway offers S3 buckets; `infrastructure.md:46` says otherwise. **Has the user verified this in the Railway dashboard, or should research re-verify before S-02's plan depends on it?** If Railway does NOT offer S3, the prod target becomes Cloudflare R2 / AWS S3 / a Railway Volume, and the docker-compose MinIO is still valid for local dev. — Owner: user. Block: yes for prod deploy, no for S-02 mock scope (FS fallback covers dev).

8. **Chart library choice.** recharts vs visx vs hand-rolled SVG. Only one chart (daily average past month) is needed. **Does the user have a preference, or should the plan pick recharts as the default (most common, React-native, fits the hand-rolled CSS aesthetic)?** — Owner: user. Block: no (plan can default to recharts).

9. **React Router scope.** S-01 deferred it to S-02/S-03. Does S-02 introduce it for the wizard routes (`/dashboard`, `/capture`, `/upload`, `/waiting/:jobId`, `/results/:jobId`), or implement the wizard as local `useState` step state (no router)? The S-01 comment suggests the router was expected by now, but a wizard is arguably a single-route state machine. — Owner: user. Block: no (plan can default to React Router for the multi-screen flow).
