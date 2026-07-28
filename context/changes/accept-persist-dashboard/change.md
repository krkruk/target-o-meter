---
change_id: accept-persist-dashboard
title: Accept scored target and persist it to the dashboard
status: implemented
created: 2026-07-28
updated: 2026-07-28
archived_at: null
---

## Notes

S-03 from roadmap.md — the north-star slice that closes the US-01 vertical (auth → photograph → detect → review → accept → dashboard updates) and is the validation milestone for the `market-feedback` sequencing goal.

Roadmap outcome: user can confirm shooting parameters (caliber, distance, weapon type), accept a detection result to persist it (or reject to discard), and see accepted results aggregated on the dashboard (total shots, last session, best result).

PRD refs: US-01, FR-009, FR-010, FR-011, FR-012 (aggregated). Prerequisite: S-02 (done).

All 7 phases implemented test-first (28/28 automated Progress rows green; SHAs
553cef5 / 8f043dc / ae9a085 / 9d56aa4 / 12439f6 / e17be54 / 53f8a71). The V-Model
system + acceptance tiers landed too:
- `tests/system/test_accept_persist_blackbox.py` — blackbox REST system test
  (live runserver, HTTP responses + stderr + SQLite-on-disk verification) that
  drives the full accept→persist→aggregate round-trip and the anon-401 gates.
  Acts as the per-phase completeness verifier for S-03's new routes.
- `src/frontend/tests-acceptance/accept-flow.spec.ts` — Playwright happy + reject
  path reusing the vision fixtures + the seeded MockDetector.

### Manual-verification posture (the 14 Manual Progress rows)

These remain operator gates — the substance of most is now pinned by automated
tests (the streamlined verification path the user chose):

- **POST-persistence items (1.7, 2.6, 2.7, 5.4, 5.5)** — covered by the blackbox
  system test (live-server round-trip + DB-on-disk reads) + the Django-test-
  client system tests. The admin-visibility checks (2.7) need `make dev` to eyeball.
- **MockDetector variance/seed (3.6)** — covered by `test_mock_detector_seed_is_
  deterministic` + `test_mock_detector_different_seeds_differ`.
- **S3 / MinIO container round-trip (4.5, 4.6, 7.6)** — the S3 byte path is pinned
  by `test_process_image_completes_under_s3_shaped_storage` (Phase 4.2) + the
  blackbox test (FS path on the live server). The actual `make dev-container`
  MinIO round-trip + presigned-URL eyeball is a genuine operator gate.
- **Dashboard desktop flow + reject + loading/error (6.6–6.9)** — covered by the
  Playwright accept-flow spec (happy + reject) + the Dashboard vitest (loading
  role=status / error role=alert).
- **infrastructure.md coherence (7.5)** — edited inline (Phase 7.1); reads with
  the corrected AGENTS.md §1.
- **Prod detector path (7.7)** — OUT OF SANDBOX. Requires a real `GOOGLE_API_KEY`
  + a real S3-compatible endpoint (Tigris or MinIO). The S-03 Phase 4 refactor
  (tempfile download for cv2.imread + presigned URLs) makes this path real; the
  verification is a manual prod-deploy gate deferred to the `/10x-deploy` chain.

Open roadmap unknowns that surfaced in planning (resolved by S-03):
- Fixed parameter list for caliber / distance / weapon type — caliber stays
  free-text (`caliber_hint`); distance + weapon_type are real ScoringJob columns
  (FR-009). The `.32ACP`/`.22LR`/`9x19mm`/`Slug` taxonomy bugs stay deferred.
- "Session" is derived (calendar day) — no new model (FR-012's "last session" =
  the most recent calendar day with ≥1 accepted result).
- Open Roadmap Question #2 (long-term image retention) stays open; S-03 matches
  the existing posture (store uploads + deliverables).
