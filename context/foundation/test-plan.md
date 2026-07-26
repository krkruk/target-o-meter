# Test Plan

> Phased test rollout for this project. Strategy is frozen at the top
> (§1–§5); cookbook patterns at the bottom (§6) fill in as phases ship.
> Read before writing any new test.
>
> Refresh: re-run `/10x-test-plan --refresh` when stale (see §8).
>
> Last updated: 2026-07-26

## 1. Strategy

Tests follow three non-negotiable principles for this project:

1. **Cost × signal.** The cheapest test that gives a real signal for the
   risk wins. Do not promote to e2e because e2e "feels safer." Do not put a
   vision model on top of a deterministic visual diff that already catches
   the regression. S-02 ships mocked detection; a fidelity test against
   `MockDetector`'s fixed pattern is tautological and is explicitly out of
   scope (deferred to S-03's real-detector wedge).
2. **User concerns are first-class evidence.** Risks anchored in "<the
   team is worried about X, and the failure would surface somewhere in
   <area>>" carry the same weight as PRD lines or hot-spot data. The
   Phase 2 interview is recorded verbatim and cited alongside PRD lines.
3. **Risks are scenarios, not code locations.** This plan documents *what
   could fail* and *why we believe it's likely* — drawn from documents,
   interview, and codebase *signal* (churn, structure, test base). It does
   NOT claim to know which line owns the failure. That knowledge is
   produced by `/10x-research` during each rollout phase. If the plan and
   research disagree about where the failure lives, research is the
   ground truth.

Hot-spot scope used for likelihood weighting: `src/` (Python: domains,
bff, target_o_meter) and `src/frontend/src/` (React SPA), excluding `cv/`
(frozen sandbox), `node_modules/`, `.venv/`, fixtures, and lockfiles.

## 2. Risk Map

The top failure scenarios this project must protect against, ordered by
risk = impact × likelihood. Risks are failure scenarios in user / business
terms, not test names. The Source column cites the *evidence that surfaced
this risk* — never a specific file as "where the failure lives" (that is
research's job, see §1 principle #3).

| #  | Risk (failure scenario)                                                                                                                                                                       | Impact | Likelihood | Source (evidence — not anchor)                                                                                                                                  |
|----|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------|------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1  | Cross-user data leak via the scoring read endpoint: user A polls another user's job id and reads their marked image + holes because the per-job ownership check is missing or maps to the wrong status. | High   | Medium     | interview Q1; PRD §Access Control ("User: own data only"); roadmap S-02 outcome; hot-spot dir `src/bff/routers/` (6 commits/30d)                                |
| 2  | BFF atomicity breach: a failure after `schedule_image_processing` enqueues the q2 task leaves an orphan `ScoringJob` row + a queued task with no owning request (BFF outer `transaction.atomic` missing/broken). | High   | Medium     | interview Q4; AGENTS.md §6.2 (BFF atomicity contract); archived `cv-service-boundary` plan; hot-spot dir `src/domains/vision/` (98 commits/30d)                 |
| 3  | Dead-end / stuck processing state: the waiting screen hangs forever (PRD §Guardrail) because stuck-job reaping isn't invoked on poll, OR a `succeeded` job renders blank because its result is null. | High   | Medium     | interview Q1 + Q4; PRD §Guardrails ("no dead-end or hang"); S-02 plan §"Open Risks"; hot-spot dir `src/domains/vision/`                                          |
| 4  | Untrusted-input / abuse on the upload endpoint: a non-image, oversized, or mass-triggered upload crashes the worker, stores junk, or denies service by exhausting the q2 cap of 3 concurrent.  | Medium | Medium     | abuse lens (untrusted input + resource abuse); PRD §NFR ("≤3 concurrent"); AGENTS.md §2; interview Q4                                                            |
| 5  | Auth/session regression on the SPA wizard: a router catch-all or OAuth-session change logs users out, serves the wrong session, or shadows `/v1`, `/login`, `/callback`, `/admin`, static.      | High   | Medium     | interview Q2 + Q3; PRD FR-001; S-02 plan §"Open Risks" (router catch-all); hot-spot dirs `src/frontend/src/` (21/30d), `src/domains/identity/` (24/30d)         |
| 6  | Storage swap misconfiguration: `USE_S3=True` in prod without the AWS vars, or the deferred S3-path refactor forgotten, silently breaks uploads in prod — works in dev only because the mock detector short-circuits before reading bytes. | High   | Low        | PRD §NFR (reliability); S-02 plan §"Critical Implementation Details" + §"Open Risks"; `infrastructure.md` (storage swap)                                          |
| 7  | Multipart/CSRF seam regression: the first multipart upload in the repo reuses the JSON `Content-Type` header (breaking the boundary) or misses the CSRF token, so every SPA upload fails in prod while JSON endpoints work. | Medium | Medium     | interview Q4; S-02 plan §"Critical Implementation Details" (CSRF on multipart); hot-spot dir `src/bff/routers/`                                                  |

### Risk Response Guidance

| Risk | What would prove protection                                                                                                                                                       | Must challenge                                                                                                                                                            | Context `/10x-research` must ground                                                                                                                                              | Likely cheapest layer                                                | Anti-pattern to avoid                                                                                                                  |
|------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------|
| #1   | A user polling another user's job id gets a 404 with NO leaked body — asserted on the response body being absent, not just the status code.                                        | "Status 404 means ownership worked" — a 404 whose JSON body still contains the other user's holes would pass a status-only check.                                          | BFF route shape; how the read accessor raises on mismatch vs. missing; the status-code mapping (403 vs 404); where the response body is serialized.                               | Integration (Django test client, two seeded users).                 | Status-only assertion; oracle lifted from the route's own return type.                                                                 |
| #2   | When the orchestrating service raises after enqueue, zero job rows AND zero queued tasks survive — asserted by row count, not by absence of an exception.                          | "The service already has an atomicity test" — that covers the *inner* savepoint; the BFF's *outer* wrap (AGENTS.md §6.2) is the new, untested layer.                       | Two-layer atomicity; the SQLite-as-broker invariant that makes the task row live in the same DB as the job row; where enqueue happens relative to the outer transaction.           | Integration (monkeypatch service to raise post-enqueue).            | Asserting only "no exception raised"; testing only one of the two rows.                                                                |
| #3   | A `succeeded` job with null result renders a clear "unable to load" state (not blank); a stuck running job past the timeout flips to failed on the next poll and the waiting screen exits to an error state. | "Status=succeeded means there is a result" — the DTO rebuild can return null on a succeeded job. Also "polling returns 200 means the user isn't stuck."                    | Where stuck-job reaping is invoked in the request path; the DTO rebuild's failure modes (malformed result JSON); the SPA poll loop's terminal transitions and cleanup on unmount. | Component (SPA state machine) + integration (reap-on-poll).         | Happy-path-only polling; asserting only the success transition; not testing cleanup.                                                   |
| #4   | A non-image / oversized upload is rejected before the task runs; the q2 cap bounds *concurrent processing* — and the test does NOT invent a new per-user rate limit that doesn't exist. | "The q2 cap of 3 protects us" — that bounds concurrent *processing*, not *upload rate* or *file validity*. A test that asserts a rate limit would be testing imagined code. | django-ninja `UploadedFile`/`Form` validation; where file bytes are first read; the q2 cap mechanism and where it enforces.                                                       | Integration (malformed/oversized bytes) + a note on the rate-limit gap. | Over-mocking the validator; testing the library rather than the seam; asserting on imagined behavior.                                  |
| #5   | A deep-link refresh on the wizard routes survives; the Django catch-all serves the SPA index for client routes AND returns the right status for `/v1/...`, `/login`, `/callback`, `/admin`, static. | "The SPA mounts means routing works" — initial mount, deep-link refresh, and API-prefix-shadowing are three separate failure modes.                                         | The catch-all URL pattern and its prefix precedence; the OAuth callback path; session cookie scope; what the SPA does on mount vs. on route change.                              | Integration (live `runserver`, prefix matrix) + component (refresh survival). | Snapshot of the router config; testing only the dashboard route.                                                                       |
| #6   | `USE_S3=True` without AWS vars fails loud at startup (a clear error, not silent misconfiguration); `USE_S3=False` preserves pre-S-02 behavior exactly; the deferred S3-path refactor is *named* in the failure message, not silently deferred. | "The settings test passed" — config-swap correctness ≠ prod-readiness; the deferred refactor is the real prod risk and must be named loudly so S-03 cannot forget it.       | The env-driven settings swap; the storage adapter's constructor branches; the path-shaped-methods guard and what it raises.                                                       | Unit (env branches) + a documented forward-reference to S-03.       | Asserting only the happy FS branch; not naming the S-03 deferral in the failure message.                                               |
| #7   | `createScoringJob` sends `FormData` with NO `Content-Type` header (browser sets the boundary) and the CSRF token present; the live-server multipart round-trip returns 201.        | "The api.test.ts spy was called" — a fetch spy asserting the call shape ≠ a real multipart request surviving CSRF + boundary on a live server.                              | The multipart headers helper; the CSRF cookie read path; django-ninja `Form`/`File` parsing; how the live-server test factory seeds the CSRF cookie.                              | Component/unit (header shape) + live-server integration (real multipart). | Mocking fetch so heavily that no real multipart request is ever shaped; asserting only the header constant.                            |

## 3. Phased Rollout

Each row is a discrete rollout phase that will open its own change folder
via `/10x-new`. Status moves left-to-right through the values below; the
orchestrator updates Status as artifacts appear on disk.

| #  | Phase name                              | Goal (one line)                                                                                          | Risks covered | Test types                                            | Status      | Change folder |
|----|-----------------------------------------|----------------------------------------------------------------------------------------------------------|---------------|--------------------------------------------------------|-------------|---------------|
| 1  | BFF scoring-routes contract             | Defend the upload→read trust boundary: ownership, atomicity, multipart, status matrix at the cheapest layer. | #1, #2, #4, #7 | integration (Django client + live `runserver`)         | change opened | context/changes/testing-bff-scoring-routes/             |
| 2  | SPA wizard state machines + router seam | Prove the user-facing flow always resolves: polling terminals, null-result fallback, deep-link refresh, prefix non-shadowing. | #3, #5        | component (vitest) + live-server integration           | not started | —             |
| 3  | Storage-swap + detector-wiring guardrails | Prove the env-driven config branches are correct and the deferred S-03 refactor is named loudly.       | #6            | unit (env branches, factory selection)                 | not started | —             |
| 4  | Quality-gates wiring                    | Lock the floor so the regressions Phases 1–3 catch can never merge back.                                 | all (regression backstop) | gate config (`make check` / `be-test` / `fe-test`)     | not started | —             |

**Status vocabulary** (fixed — parser literals):

| Value           | Meaning                                                                          |
|-----------------|----------------------------------------------------------------------------------|
| `not started`   | No change folder for this rollout phase yet.                                     |
| `change opened` | `context/changes/<id>/` exists with `change.md`; research not done.              |
| `researched`    | `research.md` exists in the change folder.                                       |
| `planned`       | `plan.md` exists with a `## Progress` section.                                   |
| `implementing`  | Progress section has at least one `[x]` and at least one `[ ]`.                  |
| `complete`      | Progress section is fully `[x]`.                                                 |

**Ordering rationale.** Phase 1 is first because the BFF scoring routes
are the new trust boundary (Risk #1 — the user's #1 worry) and the
cheapest place to catch an ownership/atomicity regression
deterministically; every later phase consumes this contract. Phase 2
depends on Phase 1's contract being pinned and covers the dead-end
guardrail (#3) plus the auth/router regression (#5). Phase 3 is lower-
likelihood (the dev path doesn't exercise the S3 branches) but high-
impact in prod and almost entirely cheap unit tests. Phase 4 codifies
what 1–3 proved and waits for those patterns to exist before wiring them
into the gate.

## 4. Stack

The classic test base for this project. AI-native tools (if any) carry a
`checked:` date so future readers can see which lines need re-verification.
Recommendations in this section are grounded in local manifests/configs
plus the MCP/tools actually exposed in the current session.

| Layer                | Tool                                              | Version    | Notes                                                                                                                                                |
|----------------------|---------------------------------------------------|------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| unit + integration   | pytest                                            | >=9.0      | Backend. Configured via `pyproject.toml [tool.pytest.ini_options]`; `dev` marker default, `uat` opt-in via `RUN_UAT=1`.                              |
| django integration   | pytest-django + Django test client                | >=4.11     | Fast in-process path for status-code matrices; `pytestmark = [pytest.mark.django_db, pytest.mark.dev]`.                                              |
| blackbox integration | live `runserver` subprocess via `runserver_factory` | (in-repo) | Real CSRF cookie path; needed for multipart + deep-link/refresh tests. Pattern lives in `tests/system/conftest.py`.                                  |
| frontend unit        | vitest (via vite)                                 | (pinned)   | `src/frontend/vite.config.ts`. Uses `vi.spyOn(api, ...)` (NOT `vi.mock`) + `@testing-library/react` accessible queries.                               |
| e2e / acceptance     | playwright + httpx                                | >=1.40     | In `system-test` dep group. Acceptance suite is empty today; reserved for true browser-level flows where integration cannot catch the failure.        |
| API mocking          | (none — in-process Django)                        | n/a        | Backend tests hit the real Django stack; no MSW/httpx-mock layer needed. Frontend component tests spy on the `api` module seam.                       |
| accessibility        | `@testing-library/react` accessible queries       | (pinned)   | Pinned by S-01: `role=` + `aria-label` assertions are load-bearing. axe-core NOT wired today.                                                        |
| (optional) AI-native | none — checked: 2026-07-26                        | n/a        | Not justified under cost × signal for Phases 1–4: the failure modes here are deterministic (status codes, row counts, state transitions).            |

**Stack grounding tools (current session):**
- Docs: none (no Context7 / framework docs MCP exposed) — relied on local manifests + the repo's already-documented test patterns (S-01/S-02 plans and `tests/system/conftest.py` are authoritative); checked: 2026-07-26
- Search: `WebSearch` + `WebFetch` + `webReader` MCP available — not needed for §4 recommendations because the local patterns already answer the stack questions (multipart CSRF, live-server factory, vitest spyOn convention); will be used by `/10x-research` if a version-specific API question surfaces during a rollout phase; checked: 2026-07-26
- Runtime/browser: none (no Playwright MCP) — playwright is in the `system-test` dep group for acceptance but is not exposed as an MCP surface in this session; not used by Phases 1–4
- Provider/platform: none (no Railway/GitHub MCP) — no quality-gate relevance for the current rollout

## 5. Quality Gates

The full set of gates that must pass before a change reaches production.
"Required for §3 Phase <N>" means the gate is enforced once that rollout
phase lands; before that, the gate is `planned`.

| Gate                                  | Where                | Required?                    | Catches                                                                 |
|---------------------------------------|----------------------|------------------------------|-------------------------------------------------------------------------|
| lint + typecheck (`make check`)       | local + CI           | required                     | syntactic / type drift, import-linter domain isolation                 |
| backend unit + integration (`make be-test`) | local + CI      | required after §3 Phase 1    | ownership, atomicity, multipart, status-matrix regressions             |
| frontend unit + component (`make fe-test`) | local + CI       | required after §3 Phase 2    | wizard state-machine + router-seam regressions                          |
| config-swap unit tests                | local + CI           | required after §3 Phase 3    | silent S3 / detector-env misconfiguration                               |
| import-linter independence contract   | local + CI           | required                     | cross-domain ORM / HTTP leakage (AGENTS.md §5)                         |
| e2e on critical flows                 | CI on PR             | optional (acceptance empty)  | browser-only failure modes not reachable via integration                |
| pre-prod smoke                        | between merge + prod | optional                     | environment-specific failures (S3 vars, q2 worker running)              |

Every row corresponds to a gate that either **is** wired (`make check`
today) or **will be wired by a named rollout phase** (Phases 1–4). No
aspirational rows.

## 6. Cookbook Patterns

How to add new tests in this project. Each sub-section is filled in once
the relevant rollout phase ships; before that, the sub-section reads
"TBD — see §3 Phase <N>."

### 6.1 Adding a backend integration test

- **Location**: `tests/system/test_<area>.py` (system/integration tests are global per AGENTS.md §4 V-Model).
- **Module marker**: `pytestmark = [pytest.mark.django_db, pytest.mark.dev]`.
- **Two styles**: Django test client for status-code matrices (fast); live `runserver` subprocess via `runserver_factory` for blackbox paths (real CSRF cookie, multipart). Mirror the two-style pattern in `tests/system/test_auth_flow.py` + `test_spa_auth_seam.py`.
- **Seed users**: `identity/test_utils.py` `make_user` / `make_owner` + the `owner_sub`/`user_sub` fixtures in `tests/system/conftest.py`. Never `factory_boy` against domain models (AGENTS.md §5).
- **Run locally**: `uv run pytest tests/system/test_<area>.py` (or `make be-test` for the full gate).
- **Detailed pattern**: TBD — see §3 Phase 1 (status-code matrix + atomicity + multipart against the scoring routes).

### 6.2 Adding a vision-domain unit test

- **Location**: `src/domains/vision/tests/test_<area>.py` (co-located with the domain per AGENTS.md §4).
- **Atomicity/orchestration reference**: existing atomicity test in `test_services_q2.py` (asserts zero orphan rows after a post-enqueue raise) is the canonical shape for any service-level rollback claim.
- **Run locally**: `uv run pytest src/domains/vision/tests/test_<area>.py`.
- **Detailed pattern**: TBD — see §3 Phase 3 (env-driven storage swap + detector-factory selection).

### 6.3 Adding a frontend component test

- **Location**: `src/frontend/src/components/<Component>.test.tsx` next to the component.
- **Mocking policy**: `vi.spyOn(api, '<fn>')` (NOT `vi.mock`) on the `api` module seam only; `beforeEach(() => vi.restoreAllMocks())`. Never mock internal components.
- **Assertions**: `@testing-library/react` accessible queries (`getByRole`, `getByLabelText`); `role=` + `aria-label` are load-bearing per S-01.
- **jsdom gaps**: mock `window.matchMedia` for viewport-branch tests (jsdom doesn't implement it).
- **Run locally**: `cd src/frontend && npm run test` (or `make fe-test`).
- **Detailed pattern**: TBD — see §3 Phase 2 (wizard state machine: queued→running→succeeded/failed, null-result fallback, deep-link refresh).

### 6.4 Adding a test for a new BFF route

- **Test type**: integration (preferred).
- **Auth shape**: `session_auth` (401 anon) on the `auth=` kwarg; `require_owner` (403) called in the body for owner-only routes. Upload routes use `session_auth` only (both roles can upload per PRD FR-006/FR-007).
- **Multipart**: do NOT reuse the JSON `Content-Type` header; set only `X-CSRFToken` and let the browser set the boundary. Live-server test required (Django test client does not exercise the real CSRF cookie path the SPA hits).
- **Reference route + tests**: TBD — see §3 Phase 1 (the scoring routes are the canonical multipart + atomicity + ownership reference once Phase 1 lands).

### 6.5 Per-rollout-phase notes

(Optional. After each phase lands, `/10x-implement` appends a 2–3 line note
here capturing anything surprising the rollout phase taught — e.g., a
fixture catalog, a non-obvious cleanup requirement, a jsdom workaround
worth reusing.)

## 7. What We Deliberately Don't Test

Exclusions agreed during the rollout (Phase 2 interview, Q5). Future
contributors should respect these unless the underlying assumption changes.

- **The `cv/` research sandbox** — frozen reference-only code (commit `76f6fc4`), explicitly out-of-scope per the S-02 plan and the ruff exclude. Re-evaluate if `cv/` is unfrozen and brought into `src/`. (Source: interview Q5.)
- **Third-party library internals** (recharts, django-storages, boto3, playwright, opencv-python-headless) — the library is its own test; assert only on the seams this project owns (config swap, factory selection, route contract). Re-evaluate if a library bug is suspected. (Source: interview Q5.)
- **CV detection fidelity (≥90%)** — deferred to S-03's real-detector wedge. S-02 ships `MockDetector`; a fidelity test against the mock's fixed pattern would be tautological. Re-evaluate when S-03 lands a real detector. (Source: S-02 plan §"What We're NOT Doing"; user's "mock the data for now" directive.)
- **OAuth provider integration (real Auth0/Google round-trip)** — `uat`-marked, gated behind `RUN_UAT=1`, hits the real provider; not worth dev-suite budget. The `dev` bypass is the dev-suite surface. Re-evaluate if the bypass diverges from the real path. (Source: `pyproject.toml` markers.)

## 8. Freshness Ledger

- Strategy (§1–§5) last reviewed: 2026-07-26
- Stack versions last verified: 2026-07-26
- AI-native tool references last verified: 2026-07-26 (none in use)

Refresh (`/10x-test-plan --refresh`) when:

- a new top-3 risk surfaces from the roadmap or archive (e.g. S-03's
  fidelity wedge, S-04's owner-destructive operations),
- a recommended tool's `checked:` date is older than three months,
- the project's tech stack changes (new framework, new test runner, the
  acceptance suite becoming non-empty),
- §7 negative-space no longer matches what the team believes.
