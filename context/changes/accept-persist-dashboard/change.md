---
change_id: accept-persist-dashboard
title: Accept scored target and persist it to the dashboard
status: implementing
created: 2026-07-28
updated: 2026-07-28
archived_at: null
---

## Notes

S-03 from roadmap.md — the north-star slice that closes the US-01 vertical (auth → photograph → detect → review → accept → dashboard updates) and is the validation milestone for the `market-feedback` sequencing goal.

Roadmap outcome: user can confirm shooting parameters (caliber, distance, weapon type), accept a detection result to persist it (or reject to discard), and see accepted results aggregated on the dashboard (total shots, last session, best result).

PRD refs: US-01, FR-009, FR-010, FR-011, FR-012 (aggregated). Prerequisite: S-02 (done).

Open roadmap unknowns that will surface in planning:
- Fixed parameter list for caliber / distance / weapon type, and whether free-text entry is allowed alongside it (FR-009).
- Whether a "session" concept is modeled (multiple targets per session) or each target persists as its own result — FR-012 references "last session" but no FR defines what a session is.
- Open Roadmap Question #2: are uploaded target images stored long-term, or only the computed score + marked image?
