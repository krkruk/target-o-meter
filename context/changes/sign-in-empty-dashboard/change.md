---
change_id: sign-in-empty-dashboard
title: Sign in via OAuth, set a username on first login, and land on an empty dashboard
status: impl_reviewed
created: 2026-07-25
updated: 2026-07-26
archived_at: null
---

## Notes

S-01 from context/foundation/roadmap.md

Roadmap outcome: sign in via OAuth, set a username on first login, and land on an empty dashboard. PRD refs: US-01, FR-001, FR-002, FR-012. Prerequisite: F-01 (`oauth-roles-scaffold`, done) — username-on-first-login UX (FR-002) was deliberately deferred from F-01 to here.

Implementation: Phases 1–3 committed (ebc4a99 p1, 127ff39 p2, cef7d23 p3 + 1e1bab8 SHA write-back). Phase 4 (real-Auth0 smoke test) confirmed by the owner end-to-end. Phase 5 (Auth0 integration hardening: dotenv, static pipeline, route prefix, owner bootstrap, dev Vite proxy) committed as ae89a16 — surfaced by the first `DEBUG=false make dev` smoke; five red-green TDD fixes. All phases implemented.
