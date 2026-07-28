# S-03 accept-persist-dashboard — Plan Brief

> Full plan: `context/changes/accept-persist-dashboard/plan.md`
> Backbone (S-02): `context/archive/2026-07-26-photo-detection-review/` (plan, plan-brief, research)

## What & Why

S-03 (`accept-persist-dashboard`) closes the US-01 vertical (auth → photograph
→ detect → review → **accept → dashboard updates**) and is the `market-feedback`
validation milestone. It adds accept/persist/aggregate on top of the S-02 vision
seam, lands the deferred prod enabler (Railway S3 + the real Google detector),
and collects the FR-009 parameters (weapon type + target_type) the wizard
skipped. The user's "70% fidelity is good enough" stance is recorded in the PRD
+ roadmap; no detector-accuracy work lands here. "Modify little to none of the
vision code" is honored everywhere *except* the storage adapter — OpenCV
fundamentally needs local bytes, so the deferred S3 path refactor in
`vision/pipeline/storage.py` is unavoidable under the Railway/Tigris prod
posture the user chose.

## Starting Point

S-02 shipped the full vision seam + SPA. The round-trip is real today:
multipart upload → q2 → `process_image` (env-driven `DetectorFactory.build`,
default `google`) → poll. Five SPA routes work end-to-end against `MockDetector`
(fixed 5-hole pattern). The Docker dev + prod-shape stacks boot. Five gaps block
S-03: no persistence/aggregation concept (dashboard reads mocked fixtures); the
S3 path raises `NotImplementedError` (prod Tigris can't process a real upload);
`distance_m` is a dead BFF field; no weapon_type + `target_type` hardcoded;
`MockDetector` is a fixed pattern. AGENTS.md §1 was already corrected to
Railway/Tigris during planning.

## Desired End State

A user uploads → polls → reviews the marked image + per-hole dropdowns + a
params form → clicks Accept (or Reject). Accept persists an immutable
`AcceptedResult` (corrected-hole snapshot + confirmed params + computed score)
and the dashboard updates immediately to show real hero stats (total shots,
last-session average, best result), a recent-results list, and a daily-average
chart — all computed on read from `AcceptedResult`. The prod path
(`VISION_DETECTOR=google` + `USE_S3=True` against Tigris) processes a real
upload end-to-end via a tempfile dance for `cv2.imread` + presigned URLs for
the marked image. The dev path keeps `MockDetector` (now random N holes,
seeded for tests) + MinIO. The PRD + roadmap record "70% good enough"; the
≥90% bar is deferred, not abandoned.

## Key Decisions Made

| Decision                          | Choice                                                                   | Why (1 sentence)                                                                                                   | Source |
| --------------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------ | ------ |
| Storage posture                   | Railway S3/Tigris; land the deferred OpenCV+S3 refactor in vision storage | User directive (corrected AGENTS.md §1); OpenCV needs local bytes so the refactor is unavoidable under USE_S3=True. | User   |
| Persistence model                 | New `AcceptedResult` sibling model (1:1 to ScoringJob by UUID)           | Clean lifecycle separation (CV pipeline vs user-confirmed score); simple aggregation queries; honors DDD §5.       | User   |
| Session concept                   | Derived = same calendar day per user (no new model)                      | Satisfies FR-012 "last session" with zero schema cost; matches "modify little".                                     | User   |
| Fidelity bar                      | Record "70% good enough"; update PRD + roadmap                           | User's stated stance; ships the MVP without touching detector code.                                                 | User   |
| Per-hole corrections              | Snapshot corrected holes at accept; immutable after                      | Honors FR-008 (correct before accept) AND FR-010 (no editing after); the dropdowns become load-bearing at accept.   | User   |
| Dev mock shape                    | Random N holes (default 10), scores 0–10, seeded for tests               | User's "10 hits between 0–10" ask; gives aggregation varied data; tests stay deterministic via seed.                | User   |
| Params collected                  | caliber + distance + weapon_type + target_type (ISSF only)               | Satisfies FR-009 fully; stays inside ISSF (AGENTS.md §2); target_type already supports both ISSF types.             | User   |
| Aggregation endpoint name         | `GET /v1/scores/aggregations` (resource-oriented, plural noun)           | New API-design lesson (URIs name resources, not actions); one round-trip; computed on read.                         | User   |
| Accept flow location              | Accept/Reject on `/results/:jobId` (extend existing screen)              | Fewest new routes; matches the S-02 results screen that already shows marked image + dropdowns.                     | User   |
| State management                  | No Redux/Oval — refetch-on-navigate                                      | S-01 deferred it; refetch suffices for the accept→dashboard propagation.                                            | Plan   |
| Taxonomy bug fixes                | Deferred (out of S-03)                                                   | User's "modify little vision code" + "70% good enough"; caliber_hint stays free-text; detector ignores it.           | Plan   |
| Per-user rate limit               | Deferred (stays TODO)                                                    | Single-user MVP; the `TODO(S-03)` at scoring_routes.py:85 stays.                                                    | Plan   |
| Image retention policy            | Deferred (Roadmap OQ #2 stays open)                                      | S-03 matches existing posture; retention lifecycle is a follow-up.                                                  | Plan   |

## Scope

**In scope:**
- `distance` + `weapon_type` columns on `ScoringJob` (migration) + threading
  through service/DTO/BFF (Phase 1)
- `AcceptedResult` sibling model + migration; `accept_job` vision service;
  `POST /v1/scoring/results` BFF route (idempotent) (Phase 2)
- `MockDetector` rewrite: random N holes, seedable for tests; migrate the
  5-hole-asserting tests (Phase 3)
- S3-compatible `ScoringStorage` refactor (byte-oriented surface + tempfile
  dance in `process_image` + Tigris presigned-URL policy) (Phase 4)
- `aggregate_for_user` service + `GET /v1/scores/aggregations` route (Phase 5)
- SPA: weapon_type + target_type in the wizard; Accept/Reject + param form on
  `/results/:jobId`; dashboard swapped from mocks to real API; new `api.ts`
  helpers (Phase 6)
- `infrastructure.md` reconciliation; Playwright accept-flow E2E; final gate;
  PRD + roadmap fidelity-decision edits (Phase 7, plus Phase 1 for the docs)

**Out of scope:**
- Closing the ≥90% fidelity wedge (decision recorded, not closed in code)
- Vision taxonomy bug fixes (`.32ACP`/`.22LR`/`9x19mm`/`Slug`)
- First-class Session model (derived from calendar day instead)
- Post-accept editing (immutable after accept)
- Long-term image retention policy / privacy posture
- Per-user submission rate limit
- CI/CD pipeline, multi-region, HA

## Architecture / Approach

Seven phases in dependency order. Phases 1–2 are the persistence core (schema +
accept service/route); Phase 3 (MockDetector) stands alone; Phase 4 (S3
refactor) is the load-bearing prod enabler, scoped to the storage adapter +
3 call sites in `process_image` only (no detector/pipeline logic changes);
Phase 5 (aggregation) builds on Phase 2's `AcceptedResult`; Phase 6 (SPA) wires
the UI to Phases 2 + 5; Phase 7 folds foundation-doc updates + E2E.

The dev loop runs `VISION_DETECTOR=mock` + `USE_S3=True` (MinIO) or `USE_S3=False`
(host FS); the prod loop runs `VISION_DETECTOR=google` + `USE_S3=True` (Tigris).
Atomicity stays two-layered (service's nested savepoint + BFF's outer
transaction). The new `POST /v1/scoring/results` is idempotent on re-POST
(returns the existing `AcceptedResult` with 200 instead of creating a
duplicate).

## Phases at a Glance

| Phase | What it delivers                                                  | Key risk                                                                                              |
| ----- | ----------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| 1. Foundation: fidelity posture + distance/weapon_type columns | PRD/roadmap decision recorded; two new columns + migration; service/DTO/BFF threading | The `distance_m` → `distance` rename must be migrated in lockstep across BFF + SPA.                   |
| 2. AcceptedResult model + accept/reject BFF routes              | New model + migration; `accept_job` service; `POST /v1/scoring/results` (idempotent)  | Idempotency-on-re-POST must handle the double-click race without a uniqueness-constraint 500.         |
| 3. MockDetector rewrite (random N holes)                        | Random N holes, seedable; 5-hole-asserting tests migrated             | Breaking the fixed-pattern tests across pytest + Playwright — seed determinism must hold everywhere.  |
| 4. S3-compatible storage refactor (the prod enabler)             | Byte-oriented storage surface; tempfile dance in process_image; presigned URLs | The `cv2.imread`-needs-local-bytes refactor is the one vision-code change "modify little" can't avoid. |
| 5. Aggregation BFF route                                        | `GET /v1/scores/aggregations`; `aggregate_for_user` service; derived-session logic | The derived-session (calendar day) grouping must match the dashboard's expectations.                  |
| 6. SPA: params + Accept/Reject + dashboard swap                 | weapon_type + target_type in wizard; Accept/Reject on /results; dashboard from real API | The accept→dashboard propagation without a store — refetch-on-navigate must suffice.                  |
| 7. End-to-end verification + foundation docs                    | infrastructure.md reconciled; Playwright accept-flow E2E; final gate   | The prod detector path (real Google key + Tigris) is a manual gate, not automatable in-sandbox.       |

**Prerequisites:** S-02 (`photo-detection-review`, done). The vision seam,
identity plumbing, SPA scaffold, Docker dev stack, and S3 config swap must all
be in place — they are.

**Estimated effort:** ~5–7 sessions across 7 phases. Phases 1 + 3 are small
(½–1 session each); Phases 2 + 5 are the backend core (1 session each); Phase
4 is the highest-risk refactor (1–2 sessions); Phase 6 is the frontend (1–2
sessions); Phase 7 is verification (½–1 session).

## Open Risks & Assumptions

- **The S3 refactor is the one vision-code change "modify little" can't
  avoid.** OpenCV's `cv2.imread` cannot read an S3 key; under the Railway/Tigris
  prod posture the user chose, the tempfile download + deliverable upload-back
  is mandatory. Phase 4 scopes it to the storage adapter + 3 call sites in
  `process_image` — no detector or pipeline logic changes — but it IS vision
  code. The tension with "modify little to none" is explicit and was surfaced
  during planning; the user chose Railway/Tigris knowing this.
- **Presigned URL expiry.** `AWS_QUERYSTRING_EXPIRE = 3600` (1 hour). A user
  who leaves the results screen open longer sees a broken `<img>` on refresh.
  Acceptable for MVP; a refresh-on-focus follow-up is post-MVP.
- **`total_shots` semantics.** The PRD says "total shots" — Phase 5.2 reads it
  as the count of holes across accepted results (not the count of accepted
  results). If the user means "number of sessions," the computation changes.
  The plan flags this for confirmation during Phase 5 implementation.
- **Tigris verification is a manual prod-deploy gate.** Phase 4 verifies
  against MinIO; the actual Tigris path can only be verified after prod deploy
  (out of S-03 scope). Phase 7 documents this.
- **The `weapon_type` list is UI-only for now.** Phase 1.7 leaves the BFF's
  `weapon_type` as free-text `str | None` to avoid a premature constraint; the
  ISSF-appropriate `WEAPON_TYPES` list lives in `taxonomy.ts`. If the BFF
  should validate against a fixed list, Phase 6 confirms the list and Phase 1
  widens the `Literal`.

## Success Criteria (Summary)

- `make check`, `make be-test`, `make fe-test` all pass; the Playwright
  accept-flow E2E drives dashboard → upload → wizard → waiting → results →
  Accept → dashboard-updated end-to-end
- A real Google-detector upload processes end-to-end against MinIO (Phase 4
  manual gate); the Tigris path is documented as a prod-deploy gate
- The dashboard shows real hero stats + recent results + daily chart
  computed from `AcceptedResult` rows (no more mocked fixtures)
- The PRD + roadmap record "70% good enough"; AGENTS.md §1 + infrastructure.md
  both reflect Railway/Tigris as prod
