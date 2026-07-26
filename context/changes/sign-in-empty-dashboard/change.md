---
change_id: sign-in-empty-dashboard
title: Sign in via OAuth, set a username on first login, and land on an empty dashboard
status: implementing
created: 2026-07-25
updated: 2026-07-26
archived_at: null
---

## Notes

S-01 from context/foundation/roadmap.md

Roadmap outcome: sign in via OAuth, set a username on first login, and land on an empty dashboard. PRD refs: US-01, FR-001, FR-002, FR-012. Prerequisite: F-01 (`oauth-roles-scaffold`, done) — username-on-first-login UX (FR-002) was deliberately deferred from F-01 to here.

Implementation: Phases 1–3 committed (ebc4a99 p1, 127ff39 p2, cef7d23 p3 + 1e1bab8 SHA write-back). Phase 4 (real-Auth0 smoke test) is reserved as the owner's out-of-session manual gate — no code, requires the owner's Auth0 tenant + credentials not present in this environment; see plan.md Phase 4 note.
