<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: S-03 accept-persist-dashboard

- **Plan**: context/changes/accept-persist-dashboard/plan.md
- **Scope**: Phases 1–8 (full plan)
- **Date**: 2026-07-28
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 1 warning, 5 observations
- **Triage status**: F1 FIXED, F3 FIXED, F4 FIXED (+tests), F5 FIXED, F6 partially (1.6/7.5 verified, 2.7 unblocked by F1); F2 deferred (operator-scale, no live defect)

## Automated verification (re-run this session)

| Command | Result | Notes |
|---|---|---|
| `make check` | PASS | ruff clean; lint-imports 2/2 contracts KEPT (Domain Isolation + BFF Above Domains); `tsc --noEmit` clean |
| `make be-test` | PASS | 199 passed in 171s — incl. `test_accept_persist_blackbox` (3, live runserver round-trip + SQLite-on-disk reads), `test_native_dev_qcluster` (2, the 8.5 qcluster red→green), `test_docker_artifacts` (19, incl. 8.7/8.8 guards), `test_aggregation_routes` (5) |
| `make fe-test` | PASS | vitest 64/64 |
| `npx playwright test` | PASS | 10/10 — incl. `accept-flow.spec` (happy + reject) + `marked-image-load.spec` (proxy URL shape, `naturalWidth>0`, no `minio`/`AWSAccessKeyId`/`Signature`) |

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | WARNING (1 finding) |
| Success Criteria | WARNING (1 finding) |

## Findings

### F1 — AcceptedResult has no /admin/ registration

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: src/domains/vision/admin.py (missing)
- **Detail**: Phase 2 manual item (Progress 2.7) requires "The AcceptedResult row is visible in /admin/vision/acceptedresult/ (add it to the admin if not auto-registered — check src/domains/vision/admin.py)". There is NO src/domains/vision/admin.py — neither AcceptedResult nor ScoringJob appears in /admin/. Django does not auto-register custom models. The identity domain sets the repo pattern (src/domains/identity/admin.py registers a read-mostly UserAdmin).
- **Fix**: Add src/domains/vision/admin.py registering read-mostly admins for ScoringJob + AcceptedResult (mirror identity/admin.py).
  - Strength: Closes the 2.7 manual gate; matches the identity-domain admin pattern already in the repo.
  - Tradeoff: One new small file; read-only fields keep it safe.
  - Confidence: HIGH — identity/admin.py is the exact template.
  - Blind spot: Decide whether holes/result JSONFields get a pretty-printer in list_display (cosmetic only).
- **Decision**: FIXED — created `src/domains/vision/admin.py` with read-mostly `ScoringJobAdmin` + `AcceptedResultAdmin` (every field read-only; `AcceptedResult` immutable-after-create per PRD FR-010). Verified via `manage.py shell` that both models register and every field is read-only. Manual gate 2.7 now satisfiable.

### F2 — Marked-image proxy buffers full PNG into memory

- **Severity**: 🔍 OBSERVATION
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/bff/routers/scoring_routes.py:180-181
- **Detail**: `get_scoring_job_marked_image` does `data = storage.read_deliverable_bytes(...)` then `HttpResponse(data, ...)` — the whole deliverable is materialized into one bytes before return. Real evidence: Playwright reports the marked image at ~1.2MB; §2 worker cap is 3 concurrent. Fine today under that cap, and the docstring honestly cites the dev_vite_proxy house style. Future scaling note (a pathological input producing a very large marked PNG could spike a worker's RSS), not a live defect.
- **Fix**: `StreamingHttpResponse` from a chunked read, or a size cap on `read_deliverable_bytes` that 413s above it. Defer to a follow-up unless MVP scale surprises.
- **Decision**: DEFERRED — real-world marked image is ~1.2MB (Playwright), 3-worker cap, no live defect. Re-surface if a user produces a pathological input.

### F3 — accept_job's IntegrityError re-fetch could raise DoesNotExist

- **Severity**: 🔍 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/domains/vision/services.py:402-408
- **Detail**: The insert-or-return-existing catch does `AcceptedResult.objects.get(source_job=..., user_uuid=...)` after an IntegrityError, assuming it's always the unique_together collision. Reviewed against the schema (models.py:101-118): every field is nullable, defaulted, or guaranteed non-null by the route contract (min_length=1 holes, Literal target_type, non-null UUIDs). Today the unique_together is the ONLY possible IntegrityError source and the re-fetch always finds the row. Latent gap is future-only — a new NOT NULL / CHECK constraint later could make the catch broader than intent and the re-fetch's DoesNotExist uncaught → 500.
- **Fix**: Wrap the re-fetch in its own `try/except DoesNotExist` that re-raises the original IntegrityError (defense-in-depth, ~3 lines).
- **Decision**: FIXED — wrapped the re-fetch in `try/except AcceptedResult.DoesNotExist: raise integrity_exc from None`; documented the future-constraint rationale inline.

### F4 — S3 read paths skip the FS branch's _safe_join containment

- **Severity**: 🔍 OBSERVATION
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/domains/vision/pipeline/storage.py:149-151, 163-165
- **Detail**: `read_upload_bytes` / `read_deliverable_bytes` under `USE_S3=True` pass `stored_path` straight to `self._storage.open(...)` with no key-shape guard, while the FS branch enforces `_safe_join` root containment. NOT exploitable today: every `stored_path` is server-composed (`save_upload`'s hex-digest+ext, `write_deliverable_bytes`'s `jobs/{job_id}/{name}`), never user-controlled. `_safe_join`'s own docstring flags "the moment a future caller passes anything user-controlled" as the trigger — the S3 branch silently forgoes that defense, and the asymmetry is the surprise.
- **Fix**: Add a key-shape guard on the S3 branch too (reject leading `/`, `..`, prefixes outside `uploads/`|`jobs/`) so the invariant holds across backends.
- **Decision**: FIXED — added `_safe_key` (S3-side counterpart to `_safe_join`) rejecting absolute/`..`/out-of-namespace keys; wired into the S3 branches of `read_upload_bytes`, `read_deliverable_bytes` (the `write_deliverable_bytes` S3 branch already constructs the key server-side from `{job_id}/{name}` so it's structurally safe). Pinned with 7 new tests in `test_storage_swap.py` (happy path for both namespaces + 5 traversal shapes).

### F5 — Late HttpResponse import in the proxy route

- **Severity**: 🔍 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/bff/routers/scoring_routes.py:177
- **Detail**: `get_scoring_job_marked_image` does `from django.http import HttpResponse` inside the function body, while the rest of the module (and sibling auth_routes.py:31) imports django.* symbols at module top. Trivial inconsistency within the same file.
- **Fix**: Hoist the import to the module top.
- **Decision**: FIXED — hoisted `from django.http import HttpResponse` to the module's top-level imports.

### F6 — Several manual Progress rows remain genuinely unchecked

- **Severity**: 🔍 OBSERVATION
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Success Criteria
- **Location**: context/changes/accept-persist-dashboard/plan.md (Progress)
- **Detail**: 14 Manual Progress rows are unchecked. Mapping:
  - 1.6, 7.5 (doc coherence) — VERIFIED this session: prd.md, roadmap.md, infrastructure.md all read coherently (fidelity "deferred not abandoned"; S3/Tigris refactor reconciled).
  - 1.7, 2.6, 3.6, 5.4, 5.5, 6.6–6.9 — substance pinned by automated tests (blackbox system test, aggregation routes, Playwright accept-flow). change.md documents this.
  - 4.5/4.6/7.6 (MinIO container round-trip + signed URL) and 7.7/8.10 (prod detector, bare-metal make dev teardown) — genuine operator gates. podman is available, but the opencv image bake is long and these are operator eyeball checks by design (marked-image-load Playwright spec 8.9 already proves the proxy delivers decodable bytes).
  - 2.7 — blocked by F1 (no vision admin.py).
- **Fix**: Check 1.6 + 7.5 now (verified); resolve F1 to unblock 2.7; leave 4.5/4.6/7.6/7.7/8.10 as operator gates and check them when next running `make dev-container` / `make prod-container`.
- **Decision**: PARTIAL — 1.6 + 7.5 verified this session (prd.md/roadmap.md/infrastructure.md all coherent: fidelity "deferred not abandoned", S3/Tigris refactor reconciled); 2.7 unblocked by the F1 admin registration; remaining items (4.5/4.6/7.6/7.7/8.10) left as genuine operator gates requiring `make dev-container` / `make prod-container` eyeball checks.

## Architectural soundness (both subagents independently confirmed)

- All Phase 1–8 contracts match implementation; no MISSING/DRIFT (Phase 4's presigned-URL consumer was intentionally superseded by the Phase 8 marked-image proxy — the AWS_QUERYSTRING_AUTH settings it depended on still landed as specified).
- DDD boundaries hold: no cross-domain ORM imports, UUIDField (not FK) for `AcceptedResult.source_job` + `.user_uuid`, DTOs only across boundaries, `@transaction.atomic` on the BFF multi-step accept.
- No "What We're NOT Doing" item violated: no Redux/Oval, no Session model, no post-accept editing, no retention policy, per-user rate limit TODO correctly deferred.
- The accept route (`session_auth` + `get_user_context` try/except→401 + `PermissionError→404` + new `StateError→409`) and aggregation route (`GET /v1/scores/aggregations`, plural noun) both honor the "resources not actions" lesson and the existing route conventions.
- Phase 8 is the strongest part: the three startup bugs (make-dev qcluster, prod staticfiles volume, concurrent-migrate race) each reproduced red→green with a system test pinning the regression; the marked-image BFF proxy is a strictly safer design than the Phase 4 presigned URL it replaced.
