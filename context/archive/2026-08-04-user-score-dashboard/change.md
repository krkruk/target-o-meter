---
change_id: user-score-dashboard
title: User score dashboard
status: archived
created: 2026-08-04
updated: 2026-08-05
archived_at: 2026-08-05T18:52:58Z
---

## Notes

The goal of this change is to build a standalone user's dashboard to review all scores, view images, and modify/delete existing scores.

### Supplemental (post-review, 2026-08-05)

Added a second E2E test, `tests/system/test_score_dashboard_upload_e2e.py` (`test_score_dashboard_full_upload_modify_delete_e2e`), covering the full user path the original Phase-5 E2E deliberately skipped by seeding: real upload → offline detect → accept → dashboard modify-to-zeros (assert average 0.0 + every persisted hole reads 0) → delete → empty state. Recorded as row 5.3 in `plan.md`. No production code or shared harness (`conftest.py`) touched; the test spawns its own `qcluster` sibling worker.
