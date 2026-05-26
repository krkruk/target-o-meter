---
project: Target-o-meter
context_type: greenfield
created: 2026-05-24
updated: 2026-05-24
product_type: web-app
target_scale:
  users: small
timeline_budget:
  mvp_weeks: 3
  hard_deadline: null
  after_hours_only: true
checkpoint:
  current_phase: 8
  phases_completed: [1, 2, 3, 4, 5, 6, 7]
  gray_areas_resolved:
    - topic: pain category
      decision: data trapped on paper targets
    - topic: insight / why now
      decision: hole detection via photo is genuinely hard CV problem
    - topic: persona scope
      decision: hobbyist niche — ISSF static competition shooters
    - topic: auth method
      decision: Google OAuth 2.0
    - topic: role model
      decision: owner + user roles; owner can manage users and restrict registration; owner also uses the app as a shooter
  frs_drafted: 12
  quality_check_status: accepted
---

# Shaping notes

Seed idea (from draft.md):

> A Web application that tracks one's shooting skills progress. A user takes a picture, the application counts all holes in a paper target and stores the result. Over time, the application shall generate a neat statistic and present the overall progress.

## Vision & Problem Statement

Shooting results are trapped on paper targets — ISSF competitive shooters have no easy way to digitize scores, aggregate them, and see trends over time. The blocker is reliable hole detection: photographing a target and accurately counting/scoring bullet holes is a genuinely hard computer-vision problem, which is why existing generic scoring tools don't work well for ISSF targets.

## User & Persona

**Primary persona:** An ISSF static competition shooter (hobbyist) who trains regularly, shoots 10m Air Pistol (170x170mm) and 25m/50m Precision Pistol (550x550mm) targets, and wants objective evidence of improvement. They photograph their targets after a session and want the app to handle scoring and trend analysis so they can focus on training.

## Access Control

Third-party OAuth sign-in. Two roles:

- **Owner** (single designated email): can list all registered users, remove them without warning, restrict registration to invite-only, AND use the app as a shooter (holds both owner and user roles simultaneously).
- **User**: can access only their own targets, sessions, and statistics. No access to other users' data.

The owner is the primary user and wears both hats.

## Success Criteria

### Primary

User authenticates via third-party OAuth, photographs an ISSF paper target, the app detects and scores bullet holes (≥90% fidelity), user reviews and confirms the result (selecting caliber, distance, weapon type), result persists, and the dashboard shows aggregated stats (total shots, last session, best result).

### Secondary

- Trend diagrams/charts over time across sessions, filterable by caliber, distance, and weapon type.

### Guardrails

  - **Privacy**: no email address stored directly — only an OAuth association. Minimal personal data collected.
- **Reliability**: no dead-end or hang during server polling — the waiting page always resolves or reports a clear error.
- **Accuracy**: hole detection fidelity ≥90% as stated in draft.

## Functional Requirements

### Authentication & Identity

- FR-001: User can sign in via a third-party OAuth provider. Priority: must-have
  > Socrates: Counter-argument considered: "excludes non-Google users."
  > Resolution: kept; the primary audience's dominant provider will be selected; other providers can be added later.

- FR-002: User can set a username/alias/nick on first login. Priority: must-have
  > Socrates: Counter-argument considered: "fragile identity — nick collisions."
  > Resolution: kept; collisions are acceptable at hobbyist scale; uniqueness enforcement is downstream detail.

- FR-003: Owner can list all registered users. Priority: must-have
  > Socrates: Counter-argument considered: "admin overhead, not core value."
  > Resolution: kept; user management is essential for the owner role in a closed community app.

- FR-004: Owner can remove a registered user without warning. Priority: must-have
  > Socrates: Counter-argument considered: "destructive with no safety net."
  > Resolution: kept; owner discretion is intentional for a small community. Consider soft-delete in future.

- FR-005: Owner can restrict registration to invite-only. Priority: must-have
  > Socrates: Counter-argument considered: "complexity overhead for MVP."
  > Resolution: kept; invite-only is a core access-control need for a closed community.

- FR-006: User can take a photo of a paper target via device camera. Priority: must-have
  > Socrates: Counter-argument considered: "excludes gallery photos."
  > Resolution: kept for MVP; gallery upload can be added as an enhancement later.

- FR-007: User can upload a target photo for hole detection and scoring. Priority: must-have
  > Socrates: Counter-argument considered: "upload bandwidth / queue bottleneck."
  > Resolution: kept; 3 concurrent uploads limit is sufficient at hobbyist scale; queuing is acceptable.

- FR-008: User can view detection results showing overall score and the target photo with marked holes. Priority: must-have
  > Socrates: Counter-argument considered: "no partial correction — all or nothing."
  > Resolution: kept for MVP; manual correction of individual holes is a v2 concern.

- FR-009: User can confirm shooting parameters (caliber, distance, weapon type) for a detection result. Priority: must-have
  > Socrates: Counter-argument considered: "fixed parameter list too rigid."
  > Resolution: kept; the initial list covers common ISSF setups; manual entry option covers the rest.

- FR-010: User can accept a detection result to persist it. Priority: must-have
  > Socrates: Counter-argument considered: "one-click persists bad data."
  > Resolution: kept; the accept/reject review step is the safety net; editing saved results is v2.

- FR-011: User can reject a detection result, discarding it without saving. Priority: must-have
  > Socrates: Counter-argument considered: "no feedback loop on rejection."
  > Resolution: kept; rejection feedback for model improvement is valuable but post-MVP.

- FR-012: User can view a dashboard with aggregated stats: total shots, last session, and best result (date + score). Priority: must-have
  > Socrates: Counter-argument considered: "stats without context are misleading."
  > Resolution: kept; dashboard shows per-parameter breakdown to provide context; drill-down is secondary.

## User Stories

### US-01: Shooter scores a target

- **Given** a logged-in user with a username set
- **When** they tap "Add results", photograph an ISSF paper target, and the server completes hole detection
- **Then** they see the overall score, the photo with marked holes, and a form to confirm caliber, distance, and weapon type

#### Acceptance Criteria
- Hole detection fidelity ≥90%
- User can accept or reject the result
- Accepted result is persisted and dashboard updates immediately

## Business Logic

The application performs visual processing to transform a photograph of an ISSF paper target into numerical score data — detecting each bullet hole and assigning a point value based on its position relative to the target's scoring rings.

Inputs: a photograph of one of two ISSF target types (10m Air Pistol 170x170mm or 25m/50m Precision Pistol 550x550mm). Output: per-hole point values (0–10 or X for center hits) plus a total score and a marked-up image showing detected hole positions. The user encounters this after photographing a target — they see the computed score and marked holes before deciding to accept or reject.

## Non-Functional Requirements

- The application detects bullet holes with ≥90% fidelity compared to manual scoring.
- No email address is stored directly; only an OAuth association links identity to user data. Minimal personal data collected.
- Up to 3 target images are processed concurrently; additional requests queue rather than overwhelm the server. This cap is adjustable once infrastructure is chosen.
- The waiting/polling page always resolves to a result or a clear error message — no dead-end states where the user is stuck without feedback.

## Non-Goals

- **No offline-first or native mobile**: The MVP is a web app requiring internet connectivity. Offline support, PWA, and native mobile apps are out of scope.
- **No additional target types beyond the two ISSF targets**: Only 10m Air Pistol (170x170mm) and 25m/50m Precision Pistol (550x550mm) are supported in v1. Additional target types can be added later.
