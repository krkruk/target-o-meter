<!-- PLAN-REVIEW-REPORT -->
# Plan Review: User Score Dashboard

- **Plan**: `context/changes/user-score-dashboard/plan.md`
- **Mode**: Deep
- **Date**: 2026-08-04
- **Verdict**: SOUND (after triage — REVISE before)
- **Findings**: 1 critical · 3 warnings · 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | WARNING (F1 — resolved: PRD amendment gate added) |
| Lean Execution | WARNING (F5 — resolved: dead `ResultsList` deletion made explicit) |
| Architectural Fitness | WARNING (F2 — resolved: route order specified) |
| Blind Spots | WARNING (F4 — resolved: `min_length=1` guard added) |
| Plan Completeness | WARNING (F3, F6 — resolved: modal shape corrected; §2.2 cleaned) |

## Grounding

23/23 paths ✓ · symbols ✓ (AcceptedResult/ScoringJob fields, DTOs, services, storage `_safe_key`) · migration# 0006 ✓ (next after 0005) · brief↔plan ✓

## Findings

### F1 — Modify feature reverses a PRD constraint, framed as a docstring tweak

- **Severity**: ❌ CRITICAL
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: End-State Alignment
- **Location**: Critical Implementation Details (immutability lift) → Phases 1.1, 2.3, 4.3
- **Detail**: The immutability of `AcceptedResult` is sourced from PRD FR-010 (`context/foundation/prd.md:97-99`: "editing saved results is v2"), echoed at `models.py:96` and `admin.py:15-16`. AGENTS.md §2 declares the PRD the source of truth for domain constraints, so reversing this constraint requires amending the PRD before the code drifts from it. The plan framed it as a docstring tweak.
- **Fix A ⭐ Recommended (APPLIED)**: Amend PRD FR-010 in a prerequisite step before Phase 2.
  - Strength: Keeps PRD-as-source-of-truth honest; records the reversal where future reviewers look; the rest of the plan then correctly reflects the amended constraint.
  - Tradeoff: Adds a small foundation/ edit step and a sign-off gate before the Modify phase can start.
  - Confidence: HIGH — AGENTS.md mandates PRD as source of truth.
  - Blind spot: Whether edit belongs in *this* milestone or a later one is a product call.
- **Fix B (rejected)**: Ship list/preview/delete now; defer Modify to a separate change — would contradict change.md's stated goal.
- **Decision**: FIXED via Fix A — added `## Prerequisites` section + `0.1` Progress step.

### F2 — `/scores/{result_id}` can shadow the existing `/scores/aggregations`

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Architectural Fitness
- **Location**: Phase 1.7 — BFF routes
- **Detail**: django-ninja matches in registration order. Existing literal `GET /scores/aggregations` (`scoring_routes.py:272-274`, mounted at `/v1/`) feeds the home Dashboard. Plan §1.7 said only "place adjacent to get_aggregations" — no before/after. If `/scores/{result_id}` (UUID) registered first, `/v1/scores/aggregations` matches the param route and fails UUID parsing (422).
- **Fix (APPLIED)**: Specified registration order in §1.7 — `/scores` (list) → `/scores/aggregations` (literal) → `/scores/{result_id}` (param); bare `/scores` is collision-free.
- **Decision**: FIXED.

### F3 — Modal state shape & prop names misdescribed vs. the actual pattern

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Plan Completeness
- **Location**: Phase 4.3 — `<ModifyModal>` + `<DeleteModal>` wiring
- **Detail**: Plan §4.3 specified `useState<{kind, row}>` and props `{target, onClose, onSuccess}`. Actual precedent (`AdminUsersPage.tsx:24,170`; `BanModal:12-16`; `DeleteUserModal:11-15`) is per-row `useState<Modal|null>` (`Modal = 'ban'|'delete'`, no `row` in state — row from closure), props `{user, onClose, onBanned|onDeleted}`. Cited line numbers accurate; shape/naming not.
- **Fix (APPLIED)**: Rewrote §4.3 — modal state lives per-row inside `<ScoreRow>` (`useState<'modify'|'delete'|null>`); modal props are `{result, onClose, onModified|onDeleted}`; `<ScoreRow>` renders the modals itself and bubbles `onModified`/`onDeleted` up. Also updated §3.4 to use `onModified`/`onDeleted` names from Phase 3 (so Phase 4 doesn't rename), and Progress 4.4.
- **Decision**: FIXED.

### F4 — `update_result` inherits a divide-by-zero the accept route is guarded against

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 2.3 (`update_result`) + 2.5 (`ScoreUpdateIn`)
- **Detail**: `score_average = sum(h.score for h in holes) / len(holes)` (services.py:404, reused in §2.3). Accept path is safe via `AcceptResultIn.holes = Field(min_length=1)` (scoring_routes.py:223); the new `ScoreUpdateIn.holes` had no guard → empty list → `ZeroDivisionError` → 500 instead of 422.
- **Fix (APPLIED)**: Added `Field(min_length=1)` to `ScoreUpdateIn.holes`.
- **Decision**: FIXED.

### F5 — Leaving `ResultsList` in place guarantees dead code

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Lean Execution
- **Location**: Phase 3.6
- **Detail**: `ResultsList` has exactly one caller (`Dashboard.tsx:14,72`), which Phase 3.6 replaces. Plan said "leave for safety / confirm in a follow-up" — guaranteed dead code, not a to-be-confirmed risk.
- **Fix (APPLIED)**: §3.6 now deletes `ResultsList.tsx` + `ResultsList.module.css` in the same step; Progress 3.6 updated.
- **Decision**: FIXED.

### F6 — §2.2 meandered between two method signatures before settling

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Plan Completeness
- **Location**: Phase 2.2
- **Detail**: §2.2 opened with `delete_deliverable_dir(self, job_id)`, weighed `listdir`, then concluded `delete_paths(self, paths)`. The discarded signature lingered and could mislead a skimming implementer. (Verification confirmed `delete_paths` taking concrete keys from the `ScoringJob` row is sound; no `listdir` needed.)
- **Fix (APPLIED)**: Rewrote §2.2 to state `delete_paths(self, paths: list[str])` as the contract up front; dropped the `delete_deliverable_dir`/`listdir` detour.
- **Decision**: FIXED.

## Verdict after triage

**SOUND** — all six findings FIXED in plan. The remaining real risk is procedural, not technical: Phase 2 is gated on the PRD FR-010 amendment landing with sign-off (Prerequisite 0.1). With that gate, the plan is safe to implement.

Mechanical Progress↔Phase contract re-verified post-edit: body sections (Prerequisites + Phase 1–4) ↔ Progress subsections all match; no stray checkboxes in phase bodies.
