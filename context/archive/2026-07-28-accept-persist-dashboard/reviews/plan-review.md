<!-- PLAN-REVIEW-REPORT -->
# Plan Review: S-03 accept-persist-dashboard Implementation Plan

- **Plan**: `context/changes/accept-persist-dashboard/plan.md`
- **Mode**: Deep
- **Date**: 2026-07-28
- **Verdict**: SOUND (after triage — all 7 findings FIXED)
- **Findings**: 1 critical · 4 warnings · 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS (was WARNING — F4, F5 fixed) |
| Blind Spots | PASS (was FAIL — F1 fixed) |
| Plan Completeness | PASS (was WARNING — F2, F3, F6, F7 fixed) |

## Grounding
Grounding: 34/35 paths ✓ (1 miss = `vision/admin.py`, an acknowledged "create if missing" item in Phase 2.6), symbols ✓, brief↔plan ✓. Deep verification confirmed every load-bearing line number (`process_image:141/146`, `_job_to_dto:282/326`, `mock_detector:29-35`, migration `0003` latest, `DetectorFactory.build` parameterless for mock).

## Findings

### F1 — Idempotent accept has a TOCTOU race; uniqueness constraint rejected for the wrong reason

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Critical Implementation Details (line ~230) + Phase 2.4 / 2.5
- **Detail**: The plan's check-then-create idempotency ("if AcceptedResult exists, return it; otherwise create") is a TOCTOU race — two concurrent POSTs (double-click / refresh-during-submit, the exact cases the plan claims to handle) both pass the existence check inside their own `transaction.atomic()` and both insert → duplicate rows. The plan rejected a uniqueness constraint on the grounds that it "would 500 on the race," but the 500 only happens if the `IntegrityError` is left uncaught; catching it and re-reading is the standard insert-or-return-existing idiom.
- **Fix A ⭐ Recommended**: Add `unique_together = ("source_job", "user_uuid")` on `AcceptedResult`; wrap the create in `try/except IntegrityError → re-fetch + return existing (200)`.
  - Strength: DB-enforced idempotency; the double-click race is impossible, not "handled." Both fields are plain UUIDFields so the constraint is trivially addable in migration 0005.
  - Tradeoff: One `except IntegrityError` branch; the 0005 migration must declare the constraint (additive, no data migration since the table is new).
  - Confidence: HIGH — canonical idempotent-insert idiom; matches the existing `PermissionError → 404` mapping style.
  - Blind spot: Only the accept create-path changes; `get_job`/`_job_to_dto` unaffected.
- **Decision**: FIXED via Fix A — Critical Impl Details + Phase 2.1 (unique_together), 2.2 (migration op), 2.4 (try/except IntegrityError → re-fetch), 2.6 (concurrent-accept regression test).

### F2 — `AcceptResultIn` DTO omits `job_id` that the route + SPA send

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness (contract break)
- **Location**: Phase 2.3 vs 2.5 vs 6.4
- **Detail**: Phase 2.3 defines `AcceptResultIn` as {target_type, caliber_hint, distance, weapon_type, holes} — no `job_id`. But Phase 2.5 says "the job_id comes from the request body" and Phase 6.4's SPA sends `{ job_id: jobId, ...payload }`. The resource-named route `POST /scoring/results` has no `{job_id}` path param, so it must be a body field the DTO doesn't declare.
- **Fix**: Add `job_id: UUID` as the first field of `AcceptResultIn` (Phase 2.3); pass it through to `accept_job`. Update the 2.6 test seed to send `job_id` in the body.
- **Decision**: FIXED — Phase 2.3 (AcceptResultIn gains `job_id: UUID`), 2.5 (route forwards `payload.job_id`), 2.6 (test bodies must include `job_id`).

### F3 — Phase 4 under-counts `absolute_path` call sites (4th at services.py:159)

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Plan Completeness
- **Location**: Phase 4.2 (Critical Implementation Details line ~210)
- **Detail**: The plan frames the refactor as "only the 3 call sites in process_image change" (`:141`, `:146`, `_rel :160-164`). Verified code shows a 4th: `storage_root = Path(storage.absolute_path(".")).resolve()` at `services.py:159`, feeding `_rel`. If Phase 4.1 makes `absolute_path` raise under S3 (the plan says old path methods "can stay as thin FS-only wrappers that raise NotImplementedError under S3"), then `:159` raises and `process_image` breaks on the S3 path — the exact thing Phase 4 exists to fix. The 4.2 snippet shows the new deliverable-write flow but doesn't call out that `:159`/`storage_root` is removed.
- **Fix**: Add to Phase 4.2's contract: line `:159` (`storage_root = Path(storage.absolute_path(".")).resolve()`) is removed entirely — `write_deliverable_bytes` returns the stored key directly, so neither `storage_root` nor `_rel` is needed. Make explicit the call sites are `:141`, `:146`, AND `:159` (the `_rel` helper at `:160-164` is deleted, not rewritten).
- **Decision**: FIXED — Phase 4.2 contract + snippet + Critical Implementation Details now enumerate `:141`/`:146`/`:159` and state `storage_root` is removed and `_rel` deleted.

### F4 — `AcceptedResult` in a new file breaks the repo's one-`models.py`-per-domain convention

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Architectural Fitness
- **Location**: Phase 2.1
- **Detail**: Phase 2.1 puts `AcceptedResult` in a new `accepted_result.py`, citing the "One class per file" lesson. But (a) there is ZERO precedent for one-model-per-file — every domain (vision, identity, core) uses a single `models.py`; (b) that lesson's context is code-gen grab-bag modules, explicitly exempting `ports.py`/`dtos.py`, not Django ORM models; (c) Django does not auto-discover models outside the app's `models` module, so a split file needs an explicit import-to-register or `makemigrations vision` silently misses it. The plan mentions `app_label = "vision"` but NOT the import-to-register requirement.
- **Fix A ⭐ Recommended**: Define `AcceptedResult` inside the existing `src/domains/vision/models.py` alongside `ScoringJob`, with `class Meta: app_label = "vision"`.
  - Strength: Matches all three existing domains; Django auto-discovers; no import-to-register footgun; migration generation "just works."
  - Tradeoff: Slight tension with the "one class per file" lesson, but that lesson is about code-gen grab-bags, not ORM models.
  - Confidence: HIGH — every domain model in the repo already does this.
  - Blind spot: If a future policy hardens "one class per file" to cover ORM models, this gets re-split — not today's rule.
- **Decision**: FIXED via Fix A — Phase 2.1 now defines `AcceptedResult` inside `models.py` alongside `ScoringJob` (matching every domain's convention; auto-discovered; no import-to-register footgun).

### F5 — Dashboard swap is a shape + scale change, not "a data-source change"

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Architectural Fitness
- **Location**: Phase 5.1 (cites mocks/dashboard.ts) + Phase 6.6
- **Detail**: Phase 5.1 claims the aggregation DTOs "mirror the shapes in mocks/dashboard.ts (the swap is a data-source change, not a shape change)." Verified fixture `ResultSummary` is `{ jobId; date; score /* 0-100 */; targetCount }`; the plan's `ResultSummaryDTO` is `{ result_id; created_at; score_average /* 0-10 */; hole_count; target_type }` — different field names, different identity, and a different SCALE (score 0-100 → 0-10). A dashboard showing "84" will show "8.4" after the swap. `ResultsList` needs a real rewrite, and the 0-100→0-10 shift is an unflagged UX change.
- **Fix**: Reword Phase 5.1 to drop the "not a shape change" claim; note the DTOs are the NEW canonical shapes and Phase 6.6 rewrites the three child components (field names + scale) to match. State `score_average` is 0-10 per the PRD's 0-10 scoring domain (the mock's 0-100 was arbitrary), so the scale change is intentional.
- **Decision**: FIXED — Phase 5.1 reworded (DTOs are new canonical shapes; 0-10 is intentional per PRD scoring domain); Phase 6.6 now states each child is rewritten, with `ResultsList`'s 0-100→0-10 scale change called out.

### F6 — `total_shots` semantics left as an implementation-time decision

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 5.2
- **Detail**: Phase 5.2 leaves "total_shots = count of accepted results vs sum of hole counts" as "decide based on the PRD's phrasing"; the brief (Open Risks) flags the same TBD. It's honestly disclosed, but it's an unresolved decision that changes the hero-stats contract and the Phase 5.4 test assertions. The plan's own text leans toward "sum of holes," which matches "total shots."
- **Fix**: Commit to "sum of hole counts across accepted results" in Phase 5.2 and write the 5.4 assertions against that.
- **Decision**: FIXED — Phase 5.2 `total_shots` pinned to sum of hole counts (not count of results); brief Open Risk updated to match.

### F7 — `accept_job` service contract omits the SUCCEEDED-status guard

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 2.4 vs 2.6
- **Detail**: Phase 2.6 says "add this guard to accept_job" (→409) for non-succeeded jobs, but Phase 2.4's service contract lists only ownership + idempotency, not the status guard. Prose is ambiguous about whether the guard lives in the service or the BFF.
- **Fix**: Add "raise `<StateError>` if job.status != SUCCEEDED" to Phase 2.4's `accept_job` contract; map it to 409 in the Phase 2.5 route (service owns the guard, matches the existing `PermissionError` pattern).
- **Decision**: FIXED — Phase 2.4 raises `StateError` on non-succeeded jobs; Phase 2.5 maps `StateError → 409` (and `PermissionError → 404`); Phase 2.6 test case updated to reference the service-owned guard.
