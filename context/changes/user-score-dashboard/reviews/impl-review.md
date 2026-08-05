<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: User Score Dashboard

- **Plan**: context/changes/user-score-dashboard/plan.md
- **Scope**: All phases (1–5, full plan review)
- **Date**: 2026-08-05
- **Verdict**: APPROVED (with minor warnings)
- **Findings**: 0 critical, 3 warnings, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

## Verification results (Step 3 — automated success criteria)

| Check | Result |
|-------|--------|
| `make check` (full gate: ruff autofix + ruff re-check + lint-imports + fe lint + fe type-check) | ✅ All 5 steps passed |
| `make be-test` (check + backend pytest) | ✅ 307 passed |
| Frontend Vitest suite (16 files) | ✅ 111 passed |
| Import-linter contracts (AGENTS.md §6) | ✅ `Enforce Domain Isolation` KEPT, `BFF Above Domains` KEPT (122 files / 145 deps) |

All automated success criteria cited in plan.md Phases 1–4 (`make check` + `make be-test` + `make fe-test`) are green. The domain-isolation invariant (AGENTS.md §5) is machine-enforced and passes.

## Findings

### F1 — `ScoreUpdateIn.target_type` missing `Literal` guard that sibling DTOs enforce

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality / Pattern Consistency
- **Location**: src/bff/routers/scoring_routes.py:352
- **Detail**: `ScoreUpdateIn.target_type` is typed `str | None`, while the two sibling request DTOs in the same file guard it with a `Literal`: `ScoringJobIn.target_type` (L84) and `AcceptResultIn.target_type` (L224) are both `Literal["air_pistol", "precision_pistol"]` with an explicit comment that the BFF MUST guard at the boundary because `AcceptedResult.target_type = models.CharField(max_length=32)` has no `choices=`. The PATCH path is the one place that field becomes mutable post-create, and it is the only one missing the guard — an owner can `PATCH {target_type: "anything"}` and it persists. It's owner-on-own-data (no privilege escalation), so WARNING not CRITICAL, but it's a direct contradiction of the documented contract two DTOs above it and a defense-in-depth hole.
- **Fix**: Change `target_type: str | None = None` → `target_type: Literal["air_pistol", "precision_pistol"] | None = None`. `from typing import Literal` is already imported (L35). The model has no `choices=`, so the BFF `Literal` is the only gate — exactly as the existing comment warns.
  - Strength: One-line change; restores parity with the two sibling DTOs and the documented boundary contract.
  - Tradeoff: None — invalid PATCH requests that previously persisted now 422 (desired).
  - Confidence: HIGH — identical pattern already used twice in the same file.
  - Blind spot: None significant.
- **Decision**: FIXED — applied at scoring_routes.py:352 (with a one-line comment cross-referencing L84/L224). New system test `test_patch_score_422_invalid_target_type` pins the 422 contract. 32 score-route tests + ruff pass.

### F2 — Home Dashboard `aggregations` go stale after in-place Modify/Delete

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/frontend/src/components/Dashboard.tsx:56-59
- **Detail**: On the home Dashboard, `handleModified` refetches `recentRows` but does NOT re-call `getAggregations()`, and `handleDeleted` optimistically filters the row out of `recentRows` without refetching `aggregations` either. Result: after modifying or deleting a score on the home page, `hero.best_result`, `hero.last_session_average`, and the `daily_averages` chart stay stale until a full page reload. Concretely: deleting your best score leaves the wrong "best_result" shown; raising a score to a new high doesn't update the hero or the daily-average chart. The `/scores` page (`ScoreDashboard.tsx`) handles this correctly via a single `load()` refetch on both callbacks — the inconsistency is only on the home Dashboard, which is the more common entry point.
- **Fix A ⭐ Recommended**: Have `handleModified` / `handleDeleted` on the home Dashboard also call `getAggregations()` (the initial `useEffect` already shows the two-call shape).
  - Strength: Keeps hero + chart consistent with the row list; matches the `/scores` page's behavior; one extra GET is cheap.
  - Tradeoff: One additional network request per in-place mutate on the home page.
  - Confidence: HIGH — the correct pattern already exists in `ScoreDashboard.tsx::load()` in this same change.
  - Blind spot: Slight duplication of the fetch orchestration between the two pages; a future shared hook could dedupe.

- **Fix B**: Document the staleness as accepted (home is read-mostly) and rely on reload.
  - Strength: No code change; minimal.
  - Tradeoff: User-visible inconsistency between the row list (fresh) and the hero/chart (stale) on the same page after a mutate — a confusing UX.
  - Confidence: MED — acceptable if the home page is positioned as a glance view.
  - Blind spot: Haven't confirmed whether the chart and hero are visually co-located with the list (if they are, the staleness is obvious to users).
- **Decision**: FIXED via Fix A — `handleModified`/`handleDeleted` on the home Dashboard now also call `getAggregations()` via a new `refetchAggregations` callback mirroring `refetchRecent` (Dashboard.tsx:49-67). New test `refetches aggregations when a row is modified or deleted (impl-review F2)` pins both modify and delete paths. Dashboard.test.tsx:9/9 pass.

### F3 — PATCH/DELETE/GET-single `/v1/scores` routes don't declare `404` in `response=`

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/bff/routers/scoring_routes.py:322 (get_score), :359 (patch_score), :386 (delete_score)
- **Detail**: The owner-domain precedent (`delete_a_user`, owner_routes.py:100-118) declares `response={204: None, 404: ErrorOut, 409: ErrorOut}` so the OpenAPI schema advertises the 404 path. The three new vision routes explicitly raise `HttpError(404, "Not found")` but declare only the success response — so the generated OpenAPI/swagger schema undersells the contract. Runtime behavior is correct (the `HttpError` fires regardless). Note vision has no `ErrorOut` DTO today, which is likely why these were omitted; `accept_scoring_result` and the owner routes both declare their non-2xx codes.
- **Fix**: Either (a) declare `404` on the three routes — adding a small `ErrorOut` to `vision/dtos.py` or reusing ninja's default error shape — or (b) accept the divergence and add a one-line comment that vision routes intentionally rely on ninja's default error rendering. (a) is more consistent; (b) is cheaper.
- **Decision**: FIXED via (a) — added a local `ErrorOut` to `vision/dtos.py` (mirrors identity's `ErrorOut` shape `{detail: str}`, kept local to avoid a cross-domain DTO import per AGENTS.md §5) and declared `404: ErrorOut` on `get_score` (scoring_routes.py:325), `patch_score` (:366), and `delete_score` (:395). OpenAPI now advertises the contract on the three new routes; runtime unchanged. Scope note: the pre-existing vision routes (`get_job`, `accept`, `aggregations`) have the same gap but are out of scope for this review — left as-is to keep the change boundary honest. 53 backend tests + 112 frontend tests + `make check` green.

### F4 — RETRACTED (false alarm — verification gate is operational)

- **Severity**: N/A — retracted
- **Detail**: Initially flagged the `Makefile` and `.importlinter` config as missing because `ls Makefile` and `make check` failed — but they failed because the reviewer's shell cwd was `src/frontend`, not the repo root. Both files exist and work: `make check` runs all 5 steps green (ruff autofix + ruff re-check + lint-imports + fe lint + fe type-check), and both import-linter contracts (`Enforce Domain Isolation`, `BFF Above Domains`) are KEPT (122 files / 145 deps analyzed). `make be-test` → 307 passed. All automated success criteria in plan.md Phases 1–4 are green and the §5 domain-isolation invariant is machine-enforced. No action.

### F5 — Shared-upload `delete` could unlink another job's bytes (latent, not active today)

- **Severity**: 📋 OBSERVATION
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding (but deferred — no action needed for MVP)
- **Dimension**: Safety & Quality
- **Location**: src/domains/vision/services.py:619, 633-642 (delete_result → delete_paths with job.input_path)
- **Detail**: `delete_result` collects `job.input_path` and deletes it post-commit (best-effort). `ScoringStorage.save_upload` dedupes by SHA-1 digest (storage.py:129), so the `uploads/{digest}{ext}` key is shared by content. If two `ScoringJob`s ever share the same upload bytes (byte-identical uploads — plausible with stock target images), deleting one result's storage would `unlink`/`delete_object` the upload out from under the other job. There's no FK linking jobs to uploads and no refcount, so nothing detects this. Today the system already accepts orphan-on-rollback, so this is latent rather than active — but it's the one place "deletes more than intended" could bite. The `ScoringJob` audit row is correctly retained (system test `test_delete_result_retains_scoring_job_audit_row` pins it), and the DB-vs-storage ordering is correct (DB delete inside `transaction.atomic`, storage delete after commit, swallowed + logged) — this finding is only about the shared-upload edge, not the core delete logic.
- **Decision**: No change for MVP. If dedup collisions become realistic (multi-user byte-identical images), refcount `uploads/` keys or restrict deletion to per-job `jobs/` deliverables and leave `uploads/` to a future sweeper.
- **Decision**: ACCEPTED (no action — observation noted for future sweeper work)

## Triage notes

- Plan drift check returned 21 MATCH / 0 DRIFT / 0 MISSING / 0 EXTRA across all phases (PREREQUISITE + 1.1–1.7 + 2.1–2.5 + 3.1–3.6 + 4.1–4.4 + 5.1). No substantive divergence from plan intent.
- Architecture rules (AGENTS.md §5) all PASS by inspection: no HTTP in domains; no cross-domain ORM imports; DTOs at boundaries (no QuerySets); `source_job`/`user_uuid` are `UUIDField` (no FK across domains); `update_result` owns `@transaction.atomic` in the service (consistent with `accept_job`); `delete_result` transaction boundary correct (DB inside atomic, S3 after commit + best-effort); ownership pattern correct on all four routes (401 missing user, 404 not-mine/not-found).
- Minor nits recorded but not escalated: (a) FS-backend `delete_upload`/`delete_paths` use `_safe_join(p).unlink(missing_ok=True)` instead of `self._storage.delete(...)` — functionally equivalent; (b) `DeleteModal` confirm button labeled "Delete permanently" vs plan's "Delete" — the E2E targets the actual label, self-consistent; (c) no dedicated `DeleteModal.test.tsx` — plan only required a ModifyModal test, and DeleteModal is covered by the Phase-5 E2E.
