---
project: Target-o-meter
version: 1
status: draft
created: 2026-07-19
updated: 2026-07-24
prd_version: 1
main_goal: market-feedback
top_blocker: skills
---

# Roadmap: Target-o-meter

> Derived from `context/foundation/prd.md` (v1) + auto-researched codebase baseline.
> Edit-in-place; archive when superseded.
> Slices below are listed in dependency order. The "At a glance" table is the index.

## Vision recap

Shooting results are trapped on paper targets; ISSF hobbyist shooters have no easy way to digitize scores, aggregate them, and see trends over time. The blocker is reliable hole detection — photographing a target and accurately counting/scoring bullet holes is a genuinely hard computer-vision problem, which is why existing generic scoring tools don't work for ISSF targets. The product wedge — the one trait that, if removed, makes the product indistinguishable from a generic scoring tool — is reliable CV-based scoring on the two supported ISSF target types (10m Air Pistol 170×170mm and 25m/50m Precision Pistol 550×550mm), delivered through an OAuth-gated web app where the owner wears both admin and shooter hats.

## North star

**S-03: Shooter accepts a scored target and sees it aggregated on the dashboard** — closes the US-01 vertical (auth → photograph → detect → review → accept → dashboard updates) and is therefore the validation milestone for the `market-feedback` sequencing goal.

> Reader-facing gloss: "north star" here means the smallest end-to-end slice whose successful delivery would prove the core product hypothesis — that reliable CV-based ISSF target scoring can be delivered as a usable web flow — placed as early as Prerequisites allow because everything else only matters if this works. A "validation milestone" is the same idea: the slice that, by landing, tells you whether the product is real. S-03 is where the chain closes; the wedge risk itself surfaces one slice earlier in S-02.

## At a glance

| ID | Change ID | Outcome (user can …) | Prerequisites | PRD refs | Status |
|---|---|---|---|---|---|
| F-01 | `oauth-roles-scaffold` | (foundation) third-party OAuth sign-in wired, user model carries a role flag, owner determinable via configured sub (OWNER_SUB_ID env var) — no username flow, no admin UI | — | FR-001, §Access Control | ready |
| F-02 | `cv-service-boundary` | (foundation) CV detection service exists alongside Django with a callable I/O contract — photo in, per-hole scores + marked image out; fidelity is downstream | — | NFR (≥90%), FR-007, FR-008 | done |
| S-01 | `sign-in-empty-dashboard` | sign in via OAuth, set a username on first login, and land on an empty dashboard | F-01 | US-01, FR-001, FR-002, FR-012 | proposed |
| S-04 | `owner-user-management` | (as owner) list registered users, remove a user, and toggle registration to invite-only | F-01 | FR-003, FR-004, FR-005 | proposed |
| S-02 | `photo-detection-review` | photograph an ISSF target, upload it, and see the detected score with holes marked for review | F-02, S-01 | US-01, FR-006, FR-007, FR-008 | proposed |
| S-03 | `accept-persist-dashboard` | confirm shooting parameters, accept or reject a detection result, and see accepted results aggregated on the dashboard | S-02 | US-01, FR-009, FR-010, FR-011, FR-012 | proposed |

## Streams

Navigation aid — groups items that share a Prerequisites chain. Canonical ordering still lives in the dependency graph below; this table is the proposed reading order across parallel tracks.

| Stream | Theme | Chain | Note |
|---|---|---|---|
| A | Auth + wedge validation | `F-01` → `S-01` → `S-02` → `S-03`; `S-04` branches from `F-01` | Main goal `market-feedback`: validation milestone closes at `S-03`; `S-04` runs in parallel once `F-01` lands |
| B | CV service | `F-02` | Joins Stream A at `S-02`; isolated because top blocker `skills` (CV novel) lives here |

## Baseline

What's already in place in the codebase as of `2026-07-19` (auto-researched + user-confirmed).
Foundations below assume these are present and do NOT re-scaffold them.

- **Frontend:** absent — no templates, no JS, no UI framework; only the Django template backend configured (`target_o_meter/settings.py:56-69`)
- **Backend / API:** partial — Django 6.0.5 scaffold only (`settings.py`, `urls.py`, `wsgi.py`, `asgi.py`); no Django apps created yet, only the `/admin` route (`target_o_meter/urls.py:20`)
- **Data:** partial — SQLite3 wired (`target_o_meter/settings.py:77-82`, Railway Volume mount path env var present); no models, no migrations beyond Django defaults
- **Auth:** partial — `django.contrib.auth` installed (`settings.py:36`) but no OAuth provider integration; no allauth / python-social-auth / OAuth views
- **Deploy / infra:** present (per `infrastructure.md`) — Railway chosen, Railpack auto-detects, `gunicorn` + `whitenoise` in `pyproject.toml`; CI/CD workflow explicitly out of MVP scope
- **Observability:** absent — no sentry / datadog / otel imports, no logging middleware

## Foundations

### F-01: OAuth + roles scaffold

- **Outcome:** (foundation) third-party OAuth sign-in (Google per `shape-notes.md`) is wired, the user model carries a role flag, and the owner is determinable via a configured designated sub (`OWNER_SUB_ID` env var) — no username flow, no admin UI, no invite-only logic; just enough that downstream slices can require authentication and check role.
- **Change ID:** `oauth-roles-scaffold`
- **PRD refs:** FR-001; PRD §Access Control (Owner/User roles)
- **Unlocks:** `S-01` (sign-in + first-login flow needs the OAuth path), `S-04` (owner admin routes need role detection)
- **Prerequisites:** —
- **Parallel with:** `F-02` (CV service boundary — independent layer, no shared state)
- **Blockers:** —
- **Unknowns:** —
- **Risk:** Sequenced first because every authenticated user flow transitively depends on it; the username-on-first-login UX (FR-002) and the owner-role admin UI are deliberately deferred to `S-01` and `S-04` respectively so this foundation stays a minimal enabler, not a full identity layer.
- **Status:** ready

### F-02: CV detection service boundary

- **Outcome:** (foundation) a callable CV detection service exists alongside Django with a documented I/O contract — accepts a target photo, returns per-hole point values (0–10 or X), a total score, and a marked-up image; the underlying detection fidelity is downstream (this foundation establishes the seam, not the accuracy).
- **Change ID:** `cv-service-boundary`
- **PRD refs:** NFR (≥90% fidelity — gates downstream slice), FR-007 (upload), FR-008 (view results)
- **Unlocks:** `S-02` (photograph + detection review consumes this service end-to-end); reduces the blocking unknown "which CV approach actually hits ≥90% on real ISSF photos" by giving that work a stable contract to iterate behind
- **Prerequisites:** —
- **Parallel with:** `F-01` (auth scaffold — independent layer)
- **Blockers:** —
- **Unknowns:**
  - Which CV approach (classical OpenCV pipeline vs. pretrained DL model vs. hybrid) should sit behind the contract? — Owner: user. Block: no (the foundation establishes the boundary; the approach is chosen inside `S-02`).
- **Risk:** Sequenced as a foundation (not folded into `S-02`) because the top blocker is `skills` (CV novel) — establishing the service seam first lets the agent/user iterate on fidelity behind a stable contract instead of conflating "what does the service expect" with "does it work". The dominant unvalidated belief — that ≥90% fidelity is achievable — remains inside `S-02`; this foundation does not pre-resolve it.
- **Status:** done

## Slices

### S-01: Sign-in flow + empty dashboard

- **Outcome:** user can sign in via the configured OAuth provider, set a username on first login, and land on an empty dashboard that renders the shell for FR-012 (no aggregated data yet).
- **Change ID:** `sign-in-empty-dashboard`
- **PRD refs:** US-01, FR-001, FR-002, FR-012 (shell)
- **Prerequisites:** `F-01`
- **Parallel with:** `S-04` (both depend only on `F-01`; different chains)
- **Blockers:** —
- **Unknowns:**
  - Is username uniqueness enforced at this stage? — Owner: user. Block: no (PRD Socrates note on FR-002: "collisions acceptable at hobbyist scale; uniqueness enforcement is downstream detail").
- **Risk:** First step on the wedge chain; sequencing this before `S-02` keeps the user-facing flow vertical (a real signed-in user lands on a real page) rather than shipping a CV spike with no UI home to return to.
- **Status:** proposed

### S-04: Owner user management

- **Outcome:** the owner can list all registered users, remove a user without warning, and toggle registration between open and invite-only.
- **Change ID:** `owner-user-management`
- **PRD refs:** FR-003, FR-004, FR-005
- **Prerequisites:** `F-01`
- **Parallel with:** `S-01`, `S-02`, `S-03` (different chain; only shares `F-01`)
- **Blockers:** —
- **Unknowns:**
  - Soft-delete or hard-delete for FR-004? — Owner: user. Block: no (PRD Socrates note: "Consider soft-delete in future" → hard-delete for MVP).
  - Invite-only mechanism — invite codes, email allowlist, or magic link? — Owner: user. Block: no (design detail resolvable in `/10x-plan`).
- **Risk:** Sequenced in parallel with the wedge chain rather than after it: owner administration has zero downstream dependents and is the only slice that does not gate the validation milestone, so it is the natural candidate to slip first if `skills`/`time` pressure forces a scope cut.
- **Status:** proposed

### S-02: Photograph target + detection review

- **Outcome:** user can capture a target photo via device camera, upload it, the CV service runs detection, and the user sees the overall score plus the target photo with holes marked — without yet persisting anything.
- **Change ID:** `photo-detection-review`
- **PRD refs:** US-01, FR-006, FR-007, FR-008; NFR (≥90% fidelity) lands here
- **Prerequisites:** `F-02`, `S-01`
- **Parallel with:** —
- **Blockers:** —
- **Unknowns:**
  - Which CV approach actually hits ≥90% fidelity on real ISSF photos for both supported target types? — Owner: user. Block: no (research-shape unknown resolved inside the plan; this is the question the whole product hinges on).
- **Risk:** This is the wedge slice — the place where the product's core hypothesis is first tested against real photographs. Under `market-feedback` + `skills`, sequencing biases toward reaching this slice as early as Prerequisites allow; if the ≥90% bar cannot be met, the roadmap must be resequenced (manual-scoring fallback, scope cut, or product rethink) before `S-03` is worth pursuing.
- **Status:** proposed

### S-03: Accept scored target + dashboard aggregation

- **Outcome:** user can confirm shooting parameters (caliber, distance, weapon type), accept a detection result to persist it (or reject to discard), and see accepted results aggregated on the dashboard (total shots, last session, best result).
- **Change ID:** `accept-persist-dashboard`
- **PRD refs:** US-01, FR-009, FR-010, FR-011, FR-012 (aggregated)
- **Prerequisites:** `S-02`
- **Parallel with:** —
- **Blockers:** —
- **Unknowns:**
  - What is the fixed parameter list for caliber / distance / weapon type, and is free-text entry allowed alongside it? — Owner: user. Block: no (PRD Socrates note on FR-009: "initial list covers common ISSF setups; manual entry option covers the rest").
- **Risk:** Closes the US-01 vertical and is therefore the validation milestone for the `market-feedback` goal. Sequenced strictly after `S-02` (no persistence before detection is trusted) — but does not carry the wedge risk itself, so it can absorb scope adjustments (e.g. simplest-possible aggregation) without threatening the product hypothesis.
- **Status:** proposed

## Backlog Handoff

| Roadmap ID | Change ID | Suggested issue title | Ready for `/10x-plan` | Notes |
|---|---|---|---|---|
| F-01 | `oauth-roles-scaffold` | OAuth + roles scaffold | yes | Run `/10x-plan oauth-roles-scaffold` |
| F-02 | `cv-service-boundary` | CV detection service boundary | yes | Run `/10x-plan cv-service-boundary`; parallel with F-01 |
| S-01 | `sign-in-empty-dashboard` | Sign-in flow + empty dashboard | no | Needs F-01 done first |
| S-04 | `owner-user-management` | Owner user management | no | Needs F-01 done first; parallel with wedge chain |
| S-02 | `photo-detection-review` | Photograph target + detection review | no | Needs F-02 + S-01; wedge slice — sequence early |
| S-03 | `accept-persist-dashboard` | Accept scored target + dashboard aggregation | no | Needs S-02; north star (validation milestone) |

## Open Roadmap Questions

1. **Is the CV ≥90% fidelity bar achievable on real ISSF photos for both target types, and what is the fallback if it isn't?** — Owner: user. Block: `S-02`, `S-03` (wedge chain). If research shows the bar is unreachable, the roadmap must be resequenced before these slices are worth planning.
2. **Are uploaded target images stored long-term, or only the computed score + marked image?** — Owner: user. Block: roadmap-wide (privacy posture per PRD §Guardrails and storage cost both ride on this; the infrastructure choice between Railway Volume and external object storage follows from it).
3. **Is a "session" concept modeled (multiple targets per session), or does each target persist as its own result?** — Owner: user. Block: `S-03` (FR-012 references "last session" but no FR defines what a session is).

## Parked

- **Offline-first / PWA / native mobile** — Why parked: PRD §Non-Goals; MVP is a web app requiring internet connectivity.
- **Additional target types beyond the two ISSF targets** — Why parked: PRD §Non-Goals; v1 ships 10m Air Pistol + 25m/50m Precision Pistol only.
- **Trend diagrams/charts over time, filterable by caliber/distance/weapon** — Why parked: PRD §Success Criteria Secondary; MVP delivers aggregated stats only.
- **Manual correction of individual holes** — Why parked: PRD FR-008 Socrates note ("v2 concern").
- **Editing saved results** — Why parked: PRD FR-010 Socrates note ("editing saved results is v2").
- **Rejection feedback loop for model improvement** — Why parked: PRD FR-011 Socrates note ("post-MVP").
- **Gallery photo upload (in addition to camera capture)** — Why parked: PRD FR-006 Socrates note ("enhancement later").
- **Dashboard drill-down** — Why parked: PRD FR-012 Socrates note ("drill-down is secondary").
- **Soft-delete for users** — Why parked: PRD FR-004 Socrates note ("Consider soft-delete in future").
- **Additional OAuth providers** — Why parked: PRD FR-001 Socrates note ("other providers can be added later").
- **CI/CD pipeline (GitHub Actions with auto-deploy)** — Why parked: `infrastructure.md` §Out of Scope; MVP deploys via `railway up`.

## Done

- **F-02: (foundation) a callable CV detection service exists alongside Django with a documented I/O contract — accepts a target photo, returns per-hole point values (0–10 or X), a total score, and a marked-up image; the underlying detection fidelity is downstream (this foundation establishes the seam, not the accuracy).** — Archived 2026-07-24 → `context/archive/2026-07-19-cv-service-boundary/`. Lesson: —.
