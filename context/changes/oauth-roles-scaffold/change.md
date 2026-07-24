---
change_id: oauth-roles-scaffold
title: OAuth + roles scaffold (F-01)
status: planned
created: 2026-07-24
updated: 2026-07-24
archived_at: null
---

## Notes

F-01 from roadmap.md. Wire Google OAuth sign-in, extend the user model with a role flag (Owner/User), and determine the owner via a configured designated sub ID (OWNER_SUB_ID env var). Minimal enabler only — no username-on-first-login UX (deferred to S-01), no admin UI (deferred to S-04), no invite-only logic. PRD refs: FR-001, §Access Control. Unlocks S-01 and S-04.