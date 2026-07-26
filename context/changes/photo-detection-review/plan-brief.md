# S-02 photo-detection-review — Plan Brief

> Full plan: `context/changes/photo-detection-review/plan.md`
> Research: `context/changes/photo-detection-review/research.md`

## What & Why

S-02 (`photo-detection-review`) connects the existing vision seam to its first
end-to-end consumer: a real BFF orchestration router over
`schedule_image_processing` / `get_job` / `reap_stuck_jobs`, exercised in dev
via the existing `MockDetector`. The S-01 SPA gains React Router + recharts and
a single-screen dashboard + capture/upload wizard. Storage swaps from
hardcoded `FileSystemStorage` to env-driven S3 (Tigris-in-prod / MinIO-locally).
The Docker dev environment F-01 deferred finally lands.

The user's "mock the data for now" directive is honored via Shape A — route
the real round-trip through `MockDetector` (selected by a new
`VISION_DETECTOR=mock` env through the existing `DetectorFactory`). S-03 flips
the env to `VISION_DETECTOR=google` + `USE_S3=True` and confronts the ≥90%
fidelity wedge.

## Starting Point

The vision seam is already built and callable (`schedule_image_processing`,
`get_job`, `reap_stuck_jobs` in `src/domains/vision/services.py`). S-01 shipped
a React+Vite SPA with an auth seam and an empty dashboard placeholder. The
storage layer hardcodes `FileSystemStorage`; `process_image` hardcodes
`GoogleAIStudioDetector`. No router, chart lib, or Docker dev env exists.
Railway now offers Tigris-backed Storage Buckets (verified), superseding the
"No managed object storage" finding in `infrastructure.md`.

## Desired End State

A user can sign in, photograph or upload an ISSF target, watch the job poll
through `queued → running → succeeded`, and see the marked image with mocked
holes + per-hole correction dropdowns (persistence is S-03) — all from a
single-screen dashboard. `make dev-container` brings up the full stack (web +
worker + MinIO + seed) against the real vision seam with mocked detection.
Both `infrastructure.md` and `AGENTS.md §1` reflect the new S3 posture.

## Key Decisions Made

| Decision                          | Choice                                                                   | Why (1 sentence)                                                                                                   | Source   |
| --------------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------ | -------- |
| BFF shape                         | Shape A (real BFF + MockDetector)                                        | Makes S-03 a one-line env flip, not a BFF rewrite; exercises real upload→poll→result round-trip in dev.            | Plan     |
| Storage split                     | Layers 1+2+3 only (config swap); defer path refactor to S-03             | OpenCV fundamentally needs local bytes (`cv2.imread` can't read S3); the full refactor is non-trivial and not needed for MockDetector. | Research |
| Detector selection                | Env-driven `VISION_DETECTOR` via `DetectorFactory`, default `google`     | `process_image` hardcodes the detector today; the factory exists but is dead code in prod — wire it in once.        | Plan     |
| Docker scope                      | F-01 deferred spec + MinIO (web, worker, minio, create-bucket)           | Picks up F-01's reviewed Docker spec verbatim; extends with MinIO for S3-compatible dev parity.                    | Plan     |
| Taxonomy home                     | UI-only, BFF mock fields, no vision change                               | The user's caliber/distance lists contradict ISSF-only vision; S-02 keeps vision pure, defers taxonomy bugs to S-03. | Plan     |
| SPA router                        | react-router-dom, five routes                                            | S-01 explicitly deferred the router to S-02; the wizard's screens should be bookmarkable + refresh-safe.           | Plan     |
| Chart library                     | recharts                                                                 | Most common React-native fit; only one chart needed (daily average past month).                                    | Research |
| Wizard route shape                | Five distinct routes (/dashboard, /capture, /upload, /waiting, /results) | Bookmarkable waiting/results; refresh during the ~30s pipeline keeps the user's place.                             | Plan     |
| Dashboard layout                  | Dedicated phase — CSS Grid, viewport-locked, tested at laptop breakpoints | Single-screen-no-scroll is the hardest layout problem in S-02 and the brief's signature constraint.                 | Plan     |
| Foundation doc updates            | S-02 updates both infrastructure.md and AGENTS.md §1                     | Both currently contradict the S3 swap; keeping them as living source of truth.                                     | Plan     |
| Fidelity posture                  | Defer to S-03 (mock only in S-02)                                        | Honors the user's "mock the data for now" directive; S-03 is the roadmap's north star for the wedge.               | Research |
| Image retention                   | Keep images (match existing vision posture)                              | S-02 exercises the real `save_upload` seam; privacy posture (Roadmap OQ #2) is a follow-up.                        | Plan     |
| Upload RBAC                       | session_auth only (both roles; per-job ownership)                        | Matches PRD FR-006/FR-007 and F-01's research; `require_owner` is not used on upload.                              | Research |
| Railway storage correction        | Tigris in prod, MinIO locally (NOT "Railway uses MinIO")                 | Official docs confirm Railway Storage Buckets are Tigris-backed; MinIO is the local-dev choice for S3 parity.      | Plan     |

## Scope

**In scope:**
- Foundation doc updates (`infrastructure.md`, `AGENTS.md §1`) + storage deps
  (`django-storages`, `boto3`) + `.env.example`
- Storage config swap (settings `STORAGES` env-driven + `ScoringStorage`
  consulting `default_storage`); path-shaped-methods refactor deferred to S-03
- Detector env wiring (`VISION_DETECTOR` via `DetectorFactory`)
- BFF scoring routes (`POST /v1/scoring/jobs`, `GET /v1/scoring/jobs/{id}`)
  with multipart upload, atomicity, ownership enforcement
- Docker dev environment (Dockerfile + dev compose + prod compose + dev-seed +
  .dockerignore) + `make dev-container` / `make prod-container` targets
- SPA router mount + multipart upload helper + five-route wizard
- Single-screen dashboard (hero stats, add-photos, results list, recharts chart)
- Capture/upload wizard, waiting screen (polling), results (marked image +
  per-hole correction dropdowns, UI-only)

**Out of scope:**
- Closing the ≥90% fidelity wedge (S-03)
- S3-compatible path-shaped-methods refactor / OpenCV tempfile dance (S-03)
- Vision-domain taxonomy work / new `distance` column (S-03)
- Persistence of accepted results, sessions, aggregation (S-03)
- Per-hole correction persistence (S-03)
- Long-term image retention policy / privacy posture (follow-up)
- CI/CD pipeline, multi-region, HA

## Architecture / Approach

Shape A: the BFF orchestrates the real vision seam; in dev the detector is
`MockDetector` (via `VISION_DETECTOR=mock`). The dev path runs on
`USE_S3=False` (FS fallback) for the host `make dev` loop, and on
`USE_S3=True` against MinIO for the `make dev-container` loop. The full
upload→poll→result round-trip is real in both; only the detector output is
mocked. S-03 flips `VISION_DETECTOR=google` + `USE_S3=True` and lands the
OpenCV-needs-local-bytes refactor.

The SPA is a viewport-locked CSS Grid dashboard with a five-route React Router
wizard hanging off it. Accessibility-first throughout (roles + aria-labels),
matching S-01's pinned conventions.

## Phases at a Glance

| Phase                                          | What it delivers                                                  | Key risk                                                                                       |
| ---------------------------------------------- | ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| 1. Foundation doc updates + storage deps       | Updated infra/AGENTS docs, django-storages + boto3, .env.example  | Doc edits read coherently with no leftover contradictions.                                     |
| 2. Storage config swap                         | Env-driven STORAGES + ScoringStorage consults default_storage     | S3 path-shaped methods break under S3 — guarded to raise NotImplementedError (S-03 refactor).  |
| 3. Detector env wiring                         | VISION_DETECTOR env → DetectorFactory in process_image            | Existing tests patch GoogleAIStudioDetector directly — must update to patch the factory.        |
| 4. BFF scoring routes                          | POST/GET /v1/scoring/jobs with multipart + atomicity              | First multipart test in the repo; atomicity contract (AGENTS.md §6.2) must hold.               |
| 5. Docker dev environment                      | Dockerfile + dev/prod compose + MinIO + make targets              | OpenCV system deps on python:3.14-slim; live-reload vs --noreload wrinkle.                     |
| 6. SPA router + api client                     | BrowserRouter + createScoringJob/getScoringJob + Sidebar.onHome   | Django catch-all must not shadow /v1, /login, /callback, /logout, /admin, static.              |
| 7. Single-screen dashboard                     | Viewport-locked CSS Grid + hero + results + recharts chart        | "No scroll" is genuinely hard; mobile falls back to scroll per the 760px convention.           |
| 8. Capture/upload wizard + waiting + results   | Five-route wizard end-to-end with mocked detector                 | `_job_to_dto` fragility can return null result on succeeded — Results must handle the fallback. |

**Prerequisites:** F-02 (`cv-service-boundary`, done) + S-01
(`sign-in-empty-dashboard`, done). The vision seam, identity plumbing, and SPA
scaffold must all be in place — they are.

**Estimated effort:** ~6-8 sessions across 8 phases. Phases 1-3 are small (1
session each); Phases 4-5 are the backend/infra core (1-2 sessions each);
Phases 6-8 are the frontend (1-2 sessions each, Phase 7 being the longest due
to the layout work).

## Open Risks & Assumptions

- **The `USE_S3=True` path is config-only until S-03.** The path-shaped-methods
  refactor (tempfile download, prefix-based containment) is deferred. This is
  sound in S-02 only because `MockDetector` short-circuits before storage is
  read for CV. S-03's real detector + S3 path MUST land the refactor together
  or `cv2.imread` will fail on a real upload.
- **`_job_to_dto` fragility** (`services.py:278-322`): the JSON→DTO rebuild
  can raise `ValueError` or return a null result on a `succeeded` job. The BFF
  + Results component must treat `result` as nullable.
- **The caliber/distance taxonomy bugs** (`.32ACP` missing, `.22LR` /
  `9x19mm` silent DEFAULT fallback, `Slug` split-brain) are real but deferred
  to S-03 where they become load-bearing. S-02's mocked detector ignores
  caliber entirely.
- **React Router deep-link refresh** requires a Django catch-all URL pattern
  serving the SPA index — it must NOT shadow the API, OIDC, admin, or static
  URL prefixes. This is a small but easy-to-get-wrong routing concern.
- **The `marked-image` surfacing** in `/results/:jobId` may need a new backend
  route (`GET /v1/scoring/jobs/{id}/marked-image`) if the deliverable path
  isn't already browser-fetchable. Confirm against Phase 4 during Phase 8.
- **Live-reload in the docker-compose dev container**: `runserver --noreload`
  is in the command (per F-01's spec), so true live-reload may need an
  entrypoint watcher or dropping `--noreload` (which introduces a dual-process
  wrinkle). Phase 5's manual verification surfaces this.

## Success Criteria (Summary)

- `make check`, `make be-test`, `make fe-test` all pass after every phase
- `make dev-container` brings up the full stack; a user can upload → poll →
  see mocked results end-to-end in the browser
- The dashboard renders single-screen (no scroll) on laptop viewports;
  falls back to scroll on mobile per the 760px convention
- `infrastructure.md` and `AGENTS.md §1` no longer contradict the S3 swap
- S-03's path forward is a documented env flip (`VISION_DETECTOR=google`,
  `USE_S3=True`) + the deferred S3-path refactor — not a BFF/UI rewrite
