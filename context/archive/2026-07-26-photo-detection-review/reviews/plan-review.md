<!-- PLAN-REVIEW-REPORT -->
# Plan Review: S-02 photo-detection-review

- **Plan**: `context/changes/photo-detection-review/plan.md`
- **Mode**: Deep
- **Date**: 2026-07-26
- **Verdict**: SOUND (post-triage — all CRITICAL/WARNING findings FIXED, F8 ACCEPTED as no-op)
- **Findings**: 1 critical, 4 warnings, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | WARNING (1 finding — F5) |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | FAIL (2 findings — F1, F2; plus F3 low-impact) |
| Plan Completeness | WARNING (3 findings — F4, F6, F8) |

## Grounding

Grounding: 17/17 paths ✓ (1 mislabel: `.env.example` exists, plan says "new"),
8/8 symbols ✓ (`schedule_image_processing`, `process_image`, `get_job`,
`reap_stuck_jobs`, `DetectorFactory.build`, `ScoringStorage`, `STORAGES`,
`_env_bool`), brief↔plan ✓.

## Findings

### F1 — Marked-image surfacing has no contract; Phase 8 builds on a guess

- **Severity**: ❌ CRITICAL
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Blind Spots
- **Location**: Phase 4.1 (response shape) × Phase 8.5 (Results.tsx)
- **Detail**: Phase 4's GET returns `ScoringJobDTO` (`vision/dtos.py:37-51`). That DTO has NO field carrying the marked-image path — only `result`, `target_type`, `caliber_hint`, `error`, `created_at`, `completed_at`. The ORM row has `marked_image_path` (`models.py:45`) but `_job_to_dto` (`services.py:278-322`) never copies it onto the DTO. So the entire `/results/:jobId` screen — the headline deliverable of the slice — has no way to obtain the marked image. Phase 8.5 acknowledges the uncertainty ("may need a new backend route … confirm against Phase 4 during Phase 8") but Phase 4 ships without resolving it.
- **Fix A ⭐ Recommended**: Add `marked_image_url: Optional[str] = None` to `ScoringJobDTO`; have `_job_to_dto` populate it; Phase 4 GET returns it; Phase 8.5 consumes it directly. No separate route in S-02.
  - Strength: One DTO field closes the contract; Phases 4 and 8 line up; no new route to design/test.
  - Tradeoff: Under prod S3 the URL field needs a presigned-URL/backend-proxy policy decision (already flagged in infra doc); S-02 ships against MinIO/FS only, so the policy is deferred correctly.
  - Confidence: HIGH — the DTO seam is the documented inter-domain contract (AGENTS.md §5).
  - Blind spot: Whether `default_storage.url(name)` works under the FS backend in DEBUG for the dev path — verify during Phase 4 (it does under FileSystemStorage; just needs MEDIA_URL configured).
- **Fix B**: Add `GET /v1/scoring/jobs/{id}/marked-image` in Phase 4 alongside POST/GET; streams bytes via a new `ScoringStorage.read_deliverable`.
  - Strength: Decouples image bytes from JSON DTO; uniform across FS/MinIO/S3.
  - Tradeoff: More surface area; conflicts with the "presigned URLs preferred" infra note.
  - Confidence: MED.
  - Blind spot: Content-Type / Range header handling.
- **Decision**: FIXED via Fix A — added Phase 4.0 sub-step (DTO field +
  `_job_to_dto` population); updated Phase 8.5 to consume
  `marked_image_url` and drop the speculative marked-image route; reconciled
  the open-risks note in plan-brief.md.

### F2 — target_type validation claim is wrong; no ORM enforcement exists

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 4.1 (ScoringJobIn + target_type note)
- **Detail**: Plan 4.1 says target_type is "validated by `schedule_image_processing`/the ORM's `Literal`-typed field." The code contradicts this: `ScoringJob.target_type` is `CharField(max_length=32)` with NO `choices=` (`models.py:39`); Django ORM doesn't read Python `Literal`. An invalid value like `"banana"` saves cleanly, enqueues, and only blows up inside `process_image` when the worker runs — a 500-shaped async failure, not a clean 422 at the BFF.
- **Fix**: Type `ScoringJobIn.target_type: Literal["air_pistol", "precision_pistol"]` so django-ninja/Pydantic enforces 422 at the request boundary; add a regression test pinning `"banana"` → 422; reword the contract note.
  - Strength: Closes the hole at the typed BFF boundary (AGENTS.md §5).
  - Tradeoff: None significant — `ScoringResultDTO.target_type: TargetType` already proves literals are enforced at the Pydantic seam.
  - Confidence: HIGH.
  - Blind spot: Confirm the front-end sends vision-domain literal values (the user's caliber list is ISSF-misaligned).
- **Decision**: FIXED — `ScoringJobIn.target_type` typed as `Literal["air_pistol", "precision_pistol"]`; Phase 4.1 contract note rewritten; new 422 regression test added to Phase 4.3 matrix (`banana` → 422). (The import-line edit this required also resolved F6 — `Schema` is now imported.)

### F3 — Phase 8.7 `failed`-state manual recipe won't trigger a failure as described

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 8.7 (manual verification)
- **Detail**: `OllamaDetector.__init__` (`ollama_detector.py:42-52`) reads `os.environ.get("OLLAMA_HOST", DEFAULT_HOST)` with `DEFAULT_HOST="http://localhost:11434"` — it does NOT raise on missing env var. The factory builds cleanly; the failure surfaces only at `detect()` time on connection-refused, after the langchain HTTP timeout — not "the factory raises" as the recipe states.
- **Fix**: Replace the recipe with `VISION_DETECTOR=banana` — the factory's `ValueError` on unknown names (`factory.py:34-36`) raises immediately at process_image, → `failed` quickly and deterministically. Or keep `ollama` and reword to say the failure surfaces at detect() time on connection-refused.
- **Decision**: FIXED — reworded the Phase 8.7 recipe (kept ollama with a dead-port note + added banana as the fast deterministic option); Progress checkbox 8.7 updated to match.

### F4 — `.env.example` is described as "new"; it already exists with content

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 1.4 (".env.example (new)")
- **Detail**: `.env.example` already exists (F-01, then S-01) carrying Auth0, OWNER_SUB_ID, DEV_AUTH_BYPASS_SUB, DEV_ADMIN_*, SECURE_COOKIES, GOOGLE_API_KEY, OLLAMA_HOST/MODEL. The plan marks it "(new)" and describes it as creating from scratch; an implementer following literally risks overwriting the existing Auth section.
- **Fix**: Reword Phase 1.4's File line to "`.env.example` (extend)" and note Auth + Detector (GOOGLE/OLLAMA) sections already exist; this phase APPENDS the Storage section and the VISION_DETECTOR value commentary.
- **Decision**: FIXED — Phase 1.4 reworded to "(extend)"; contract now says APPEND a Storage section, lists the existing Auth + Detector sections to leave untouched, and reserves the VISION_DETECTOR slot for Phase 3.3.

### F5 — Phase 3 under-weights breakage to 4 existing q2 tests

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: End-State Alignment
- **Location**: Phase 3.1 / 3.2 + success criteria
- **Detail**: Phase 3.1 removes the `GoogleAIStudioDetector` import from services.py. Four existing tests in `test_services_q2.py` (lines 66-67, 124, 148, 201-202) patch `src.domains.vision.services.GoogleAIStudioDetector` directly — that patch target disappears. Phase 3's success criteria say only "verify they still work OR update them." ALL FOUR tests WILL break with `AttributeError`; the rewrite is mandatory, not optional. The "prefer" prose hides this.
- **Fix**: Reword Phase 3's criteria to make the rewrite mandatory: "Update the 4 existing tests in `test_services_q2.py` (lines 66-67, 124, 148, 201-202) to patch `src.domains.vision.services.DetectorFactory.build` returning the existing MockDetector/_Sentinel instances — they WILL break otherwise." Promote the rewrite to a numbered Phase 3 sub-step.
- **Decision**: FIXED — added Phase 3.2a (mandatory rewrite with the 4 specific patch sites + the new patch target); reworded Phase 3 success criteria (no more "verify they still work"); added a Progress checkbox (3.2) and renumbered the manual items (3.4 / 3.5).

### F6 — Phase 4.1 code block omits `Schema` from the ninja import

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 4.1 — scoring_routes.py code block
- **Detail**: Contract code defines `class ScoringJobIn(Schema):` / `class ScoringJobOut(Schema):` but `from ninja import ...` pulls only `Router, Form, File`. Pasting verbatim yields `NameError: name 'Schema' is not defined`.
- **Fix**: Add `Schema` to the `from ninja import ...` line in the code block.
- **Decision**: FIXED — resolved by F2's import-line edit (`from ninja import Router, Form, File, Schema`).

### F7 — BFF error-mapping inconsistency with existing routes

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Architectural Fitness
- **Location**: Phase 4.1 — get_scoring_job
- **Detail**: Existing BFF routes (session_routes.py:42-43, 59-60; api.py:58-61) consistently wrap `get_user_context` in try/except and map `User.DoesNotExist → HttpError(401, "Session user no longer exists")`. Phase 4.1's `get_scoring_job` calls `get_user_context` without that guard. A deleted-between-auth-and-body session sub (S-04) would raise unhandled `DoesNotExist` → 500, where every other BFF route returns 401.
- **Fix**: Wrap the `get_user_context(...)` call in `get_scoring_job` with the same `try/except User.DoesNotExist → HttpError(401, ...)` pattern used in session_routes.
- **Decision**: FIXED — guard added to BOTH `create_scoring_job` and `get_scoring_job`; `get_user_model` import added to the Phase 4.1 code block; contract prose documents the convention.

### F8 — recharts / react-router-dom version-pin note

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 6.1 (package.json)
- **Detail**: Phase 6.1 pins `react-router-dom: ^6.26.0` and `recharts: ^2.12.0`. Both are fine for React 18.3 today (2026-07). Flag only: react-router v7 is out; the `^` allows minor/patch bumps within the major, which is correct.
- **Fix**: No change; verify the installed versions during Phase 6's `npm install` step.
- **Decision**: ACCEPTED — no plan edit; the existing note already says "verify the installed versions during Phase 6's `npm install` step".
