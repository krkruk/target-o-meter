---
change_id: oauth-roles-scaffold
title: OAuth + roles scaffold (F-01)
status: archived
created: 2026-07-24
updated: 2026-07-25
archived_at: 2026-07-25T12:53:41Z
---

## Notes

F-01 from roadmap.md. Wire Google OAuth sign-in, extend the user model with a role flag (Owner/User), and determine the owner via a configured designated sub ID (OWNER_SUB_ID env var). Minimal enabler only — no username-on-first-login UX (deferred to S-01), no admin UI (deferred to S-04), no invite-only logic. PRD refs: FR-001, §Access Control. Unlocks S-01 and S-04.

## Implementation review (2026-07-25)

Review at `reviews/impl-review.md`. Verdict: NEEDS ATTENTION → resolved. 6 warnings, 5 observations; 0 critical.

- F1 FIXED — `SECURE_PROXY_SSL_HEADER` gated on `SECURE_COOKIES` (Render TLS-terminator-aware `request.is_secure()`).
- F2 FIXED — `User.DoesNotExist` in `/api/me` + `require_owner` mapped to `HttpError(401)`; 2 regression tests added.
- F3 FIXED — `make_owner` is now a pure row factory (no `os.environ` mutation).
- F4 FIXED — admin permission fields (`is_staff`, `is_superuser`, `groups`, `user_permissions`) moved to `readonly_fields`.
- F5 DISMISSED — identity README follows the vision README pattern (premise was wrong).
- F6 FIXED — `create_superuser(self, sub, nick="", password="", **extra)` matches Django's positional contract.

Final gates: 67 tests pass, ruff clean, both import-linter contracts KEPT, system check clean.