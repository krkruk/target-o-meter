# GitHub Actions CI/CD + Railpack Railway IaC — Plan Brief

> Full plan: `context/changes/infrastructure-as-code/plan.md`
> Research: `context/changes/infrastructure-as-code/research.md` (primary + Follow-up #1 Free-tier + Follow-up #2 decisions locked)

## What & Why

Build a modular GitHub Actions CI/CD pipeline (composite actions under `.github/actions/`, PR gate + auto-deploy on master merge) and a Railpack-driven Railway IaC for the single-service Free-tier topology. The deploy path pivots from the originally-researched Dockerfile path to **Railpack-native deploy with uv** per user direction, overriding `infrastructure.md:93`. Production secrets are provisioned manually in dashboards — the agent never touches a prod token.

## Starting Point

- `.github/workflows/uat.yml` is the only existing workflow (stale `setup-uv@v3`, gated on `vars.UAT_ENABLED == 'true'`, no actual UAT test file to run).
- Root `Dockerfile` has `dev` + `prod` stages; both `docker-compose.{dev,prod}.yml` use it for **local dev only**.
- No `.railway/` IaC exists; the research draft is the starting point (adapted from Dockerfile-path to Railpack).
- `pyproject.toml` name is `module1` (placeholder), making `RAILPACK_DJANGO_APP_NAME` load-bearing under Railpack per `lessons.md:5-10`.
- No `/health` endpoint exists (`urls.py:20-24`); Railway has no readiness signal.

## Desired End State

Pushing to a PR runs lint → unit (BE ∥ FE) → system; merging to master runs the same chain plus Playwright acceptance and auto-deploys to Railway via `railway up`. The Railway project is a single-service Free-tier deployment (gunicorn + qcluster in one container, 512MB Volume, Tigris bucket), declared in `.railway/railway.ts` and built via `railpack.json`. The user manages all production secrets in dashboards; the IaC uses `preserve()` placeholders.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Build path | Railpack (not Dockerfile) | User direction: "deploy bare metal with uv, not Docker"; the original OpenCV-build concern is mitigated by `RAILPACK_DEPLOY_APT_PACKAGES`. | Plan (user override of Research) |
| Topology | Single-service `web` | 2-service is provably broken for SQLite + django-q2 ORM broker (1 Volume → 1 service). | Research |
| Acceptance in CD | JS Playwright E2E in dev mode | Zero secrets, zero Google AI cost (`VISION_DETECTOR=mock`); the Python UAT has no test file yet. | Research (Follow-up #2) |
| CI topology | Chained jobs via `needs:` | Satisfies "sequential" semantically while enabling parallel unit stage; cleaner failure isolation. | Plan |
| Deploy gating | Auto-deploy after tests pass | ≤10-user MVP scale; rollback is the recovery path; no required-reviewer friction. | Plan |
| Tier target | Free only (Trial credit consumed) | User: "attempt free tier first; Hobby ($5) is the contingency." | Plan (user override of Research) |
| Q2 workers | 1, env-overridable | Free 0.5GB RAM cap; q2 worker subprocess is the single biggest RAM consumer after gunicorn. | Research (Follow-up #2) |
| `/health` view | Add now | Railway needs a real readiness signal for clean sleep/wake + volume-attached redeploy. | Plan |
| `uat.yml` | Delete | Redundant (no test file, never enabled, stale uv pin); re-add via composites when a real UAT test exists. | Plan (user direction) |
| Dockerfile | Keep for local dev, `BUILDER=railpack` on Railway | Preserves existing `docker compose` dev workflow; forces Railpack over Dockerfile auto-detection. | Plan |
| Railpack config | `railpack.json` in repo | Declarative, version-controlled, survives re-clones; some config is only expressible in the JSON, not env vars. | Plan |
| Python 3.14 | Pin, accept compile fallback | Version parity across local/CI/prod; pyproject already `requires-python>=3.14`. | Plan |
| IaC verification | Manual dashboard provisioning first, IaC reconciles after | No dry-run for `config apply`; preserve() guards manually-set secrets; keeps all prod tokens out of agent context. | Plan (user direction) |
| `config apply` in CI | Never — CI only runs `railway up` | IaC reconciliation is a manual one-shot; deploys don't drift infrastructure. | Plan |
| Verify-on-apply items | Inline checklist as Phase 8 | No Trial cushion; every verify item (bucket creds, Tigris endpoint, image size, cold-start) is load-bearing. | Plan |

## Scope

**In scope:**
- 8 GitHub Actions composite actions (`.github/actions/<name>/action.yml`)
- 2 workflows (`ci.yml` on PR, `cd.yml` on master push)
- `railpack.json` at repo root (Python 3.14 pin, opencv apt deps, npm + collectstatic build steps, WSGI app name)
- `.railway/railway.ts` (single-service `web`, Volume, bucket, env block with preserve())
- `/health` view + `Q_CLUSTER` env-overridable + delete `uat.yml`
- Manual provisioning checklist (Railway dashboard + GitHub secrets/vars + branch protection)
- First-deploy verification (4 verify-on-apply items + Free-RAM contingency)

**Out of scope:**
- Postgres migration / 2-service split (documented as future evolution)
- Trial-tier or Hobby-tier verification (Free only; Hobby is the documented escape hatch)
- External health-check ping / keep-warm (defeats sleep-mode RAM savings)
- Python UAT re-introduction (no test file exists)
- `supervisord` / `s6-overlay` (MVP ships `sh -c`; supervisord is a fast-follow)
- Required-reviewer deploy gate (auto-deploy posture)
- Free→Hobby transition verification (operational, not a plan milestone)

## Architecture / Approach

```
PR/master push
  │
  ▼
lint ──► (be-unit ∥ fe-unit) ──► system ──► [CD only] acceptance ──► [CD only] deploy
                                                                        │
                                                                        ▼
                                                            railway up (production env)
                                                                        │
                                                                        ▼
                                                Railpack build from railpack.json
                                                (Python 3.14 + opencv apt + npm + collectstatic)
                                                                        │
                                                                        ▼
                                            gunicorn + qcluster on single Free-tier service
                                            Volume=/data (SQLite), Bucket=uploads (Tigris)
```

The human provisions Railway project + secrets manually once; thereafter CI runs `railway up` only (no IaC reconciliation in the deploy path). The IaC (`.railway/railway.ts`) is the source of truth, applied once locally by the human via `railway config apply` — `preserve()` protects manually-set secrets from clobbering.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Django Foundation | `/health` view, `Q2_WORKERS` env-overridable, delete `uat.yml` | `Q2_WORKERS` inert until IaC sets it; deletion loses the (unused) UAT runner |
| 2. Railpack Config | `railpack.json` with Python 3.14, opencv apt, npm+collectstatic, WSGI name | Python 3.14 precompiled-binary availability; railpack.json schema is young |
| 3. CI Composites | 8 reusable actions under `.github/actions/` | opencv apt list must match Dockerfile exactly; `setup-uv@v6` (not v3) |
| 4. CI Workflow | `ci.yml` — PR gate, chained jobs, cancel-in-progress | Branch protection config is manual, not in the workflow file |
| 5. CD Workflow | `cd.yml` — push-to-master chain ending in auto-deploy | `environment: production` must be created in GitHub or deploy can't read token |
| 6. Railway IaC | `.railway/railway.ts` — single-service Free-tier topology | `preserve()` semantics on bucket creds unconfirmed (Open Q #3) |
| 7. Manual Provisioning | Every Railway + GitHub secret/var/env created by human | Many chicken-and-egg dependencies; agent must not touch any secret |
| 8. First Deploy Verify | `config apply` + first deploy + 5-item verification checklist | Free-RAM headroom is tight (~250-400MB / 512MB); Hobby escape hatch documented |

**Prerequisites:** Railway account (Free tier reachable); GitHub repo admin (for secrets/vars/environments/branch protection); Auth0 tenant + Google AI Studio key + scoped Railway token all provisioned by hand.

**Estimated effort:** ~3-4 sessions — one for Phases 1-3 (foundation), one for Phases 4-6 (workflows + IaC), one for Phase 7 (dashboard provisioning), one for Phase 8 (first deploy + verification). Phases 7-8 are operational and largely human-driven.

## Open Risks & Assumptions

- **`preserve()` on bucket creds is unconfirmed** (Open Q #3 from research) — if `railway config apply` clobbers `AWS_*` on first run, the human must re-set them and the IaC needs an explicit ref added. Phase 8 verifies.
- **Python 3.14 may not have a precompiled Mise binary** on Railpack's first build — `MISE_PYTHON_COMPILE=1` is the documented fallback but adds 5-10min to the build and may surface wheel-availability gaps in opencv/scipy on a fresh 3.14. Phase 8 verifies.
- **Railpack Node+Python build sequence is newer than its pure-Python story** — the frontend build step may need iteration on first run (npm cache, node version pinning). Phase 8 verifies image size and bundle correctness.
- **Dockerfile ↔ railpack.json drift** is now possible — any change to apt deps, build sequence, or start command must land in both files. Mitigation to be captured in AGENTS.md / lessons.md.
- **`infrastructure.md:93` Risk Register entry is now stale** — must be updated to reflect the Railpack-with-explicit-apt-deps mitigation.
- **Free-RAM headroom is tight** — ~250-400MB resident against the 512MB cap. If a CV job OOMs, the fallback ladder is `Q2_WORKERS=2` then Hobby ($5/mo).

## Success Criteria (Summary)

- A PR with failing lint/unit/system cannot merge (branch protection enforces it).
- A merge to master auto-deploys to Railway within ~15-20 minutes with no human intervention.
- The deployed app passes `/health`, serves the SPA, completes a test upload through to Tigris-backed S3, and processes a CV job without OOM.
- Every production secret was set by the human in a dashboard; no secret value ever entered the plan, the IaC, or the agent's context.
- The 4 verify-on-apply items are each either verified or have their documented fallback action taken.
