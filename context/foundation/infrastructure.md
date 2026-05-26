---
project: Target-o-meter
researched_at: 2026-05-26
recommended_platform: Railway (EU West Metal — Amsterdam, europe-west4-drams3a)
runner_up: Render
context_type: mvp
region_constraint: europe-west4-drams3a
tech_stack:
  language: Python
  framework: Django 6.0.5
  runtime: CPython
  database: SQLite3 (via Railway Volume)
---

## Recommendation

**Deploy on Railway (EU West Metal — Amsterdam, Netherlands).**

Railway scored 4/5 on agent-friendly criteria with the only gap being agent-readable docs (no `llms.txt`). It offers first-class Django support via Railpack (auto-detects `manage.py`, supports `uv`), persistent processes for background workers, and an official GA MCP server. The app uses SQLite3 on a persistent Railway Volume — no managed database service needed, which keeps the cost at $5-8/month on the Hobby plan. The user swapped from Render (the scoring leader at 5/5) after the anti-bias cross-check surfaced a $20/month realistic cost vs. Railway's $5-8/month, making cost the deciding factor. All services must be deployed to the **EU West Metal (Amsterdam, Netherlands, `europe-west4-drams3a`)** region to keep latency low for European users and comply with EU data residency preferences.

## Platform Comparison

| Platform | CLI-first | Managed/Srvless | Agent docs | Stable deploy | MCP/Integration | Total |
|---|---|---|---|---|---|---|
| Render | Pass | Pass | Pass | Pass | Pass | 5P |
| Railway | Pass | Pass | Partial | Pass | Pass | 4P 1Pa |
| Fly.io | Pass | Pass | Partial | Pass | Partial | 3P 2Pa |
| Cloudflare | Partial | Partial | Pass | Partial | Pass | 2P 3Pa |
| Vercel | Pass | Partial | Partial | Pass | Partial | 2P 3Pa |
| Netlify | Pass | Partial | Pass | Partial | Pass | 2P 3Pa |

**Notes:**

- **Cloudflare**: Django cannot run on Workers (Pyodide/WASM runtime lacks threading, sockets, C extensions). The Containers path is GA but immature for Django. Dropped on tech-stack hard constraint.
- **Vercel**: First-class Python/Django support as serverless functions, but no persistent processes, no WebSocket, ephemeral filesystem. Dropped on Q1 persistent-connection hard filter.
- **Netlify**: No Python runtime at all (JS/TS/Go only). Hard-dropped on tech-stack constraint.
- **Fly.io**: Full Docker-based Django support, persistent VMs, multi-region. But managed Postgres is $38/month (Basic), docs lack `llms.txt`, and MCP integration is less mature. Competitive on raw compute cost but expensive with managed services.
- **Render**: Perfect 5/5 score. Native Python runtime, `llms-full.txt` docs, GA MCP server, managed Postgres + Redis. But realistic production cost (web $7 + worker $7 + DB $6 = $20/mo) is 2-3x Railway for the same Django workload.
- **Railway**: Railpack auto-detects Django, supports `uv`, GA MCP server (local + remote), persistent processes, serverless sleep mode. No managed database needed — SQLite3 on a Railway Volume is sufficient for MVP scale. Only gap: no formal `llms.txt` (docs are markdown on GitHub). Hobby at $5/month with $5 usage credit is the cheapest viable option.

### Shortlisted Platforms

#### 1. Railway (Recommended)

Won on the combination of cost, Django-first-class support, and agent tooling. Railpack builder auto-detects Django and handles `uv`-based projects. The app uses SQLite3 on a persistent Railway Volume — no managed database needed, which eliminates $6-38/month in database costs. The official MCP server (local + remote at `mcp.railway.com`) gives agents structured access to deploys, logs, env vars, and service management. The $5/month Hobby plan covers a small Django MVP with headroom. The cost advantage over Render ($5-8/mo vs $13-20/mo) was decisive given the user's cost-minimization priority.

#### 2. Render

Scored highest on agent-friendly criteria (5/5) with `llms-full.txt` docs and a mature MCP server. Native Python runtime with `uv` support, managed Postgres, Redis-compatible Key Value, and background workers as a first-class service type. The gap vs. Railway is purely cost: $13-20/month realistic production cost vs. $5-8. Also has superior documentation accessibility (single-file `llms-full.txt` vs. crawling GitHub repo). If cost were not the top priority, Render would be the pick.

#### 3. Fly.io

Lowest floor cost ($2/month for a tiny VM) with persistent Docker containers and multi-region deployment. Django has an official framework guide. However, managed Postgres at $38/month (Basic HA) makes co-located database expensive, docs lack `llms.txt`, and the MCP server is less documented than Railway/Render. Best fit for cost-sensitive users who can self-manage Postgres or don't need managed services.

## Anti-Bias Cross-Check: Railway

### Devil's Advocate — Weaknesses

1. **No `llms.txt` docs** — agents must crawl the GitHub docs repo rather than loading a single structured file. This causes occasional CLI hallucination (outdated Nixpacks syntax, wrong command flags).
2. **$5 Hobby credit may not cover full stack** — Django + Celery worker for image processing likely costs $5-8/month in resource usage. SQLite on a Volume eliminates managed database costs entirely.
3. **No managed object storage** — same gap as Render. Uploaded target images need external S3, Cloudflare R2, or Railway Volumes (persist across deploys with explicit volume mounts).
4. **Only 4 regions** — limited to US West, US East, EU West Metal (Amsterdam, `europe-west4-drams3a`), SE Asia. No Central/Eastern European region for the developer's location (Poland); Amsterdam is the closest available EU option.
5. **Serverless sleep mode returns 502 on first request** — if enabled to stay within $5 credit, users experience cold-start failures on their first action.

### Pre-Mortem — How This Could Fail

The team picked Railway for the $5/month price tag. Django deployed smoothly with Railpack auto-detection. SQLite on a persistent Volume kept database costs at zero. But a Celery worker for image processing pushed the real bill to $8-12/month — cheaper than Render's $20, but above the $5 credit. The missing `llms.txt` meant the AI agent periodically hallucinated Railway CLI commands, using outdated Nixpacks syntax instead of the newer Railpack builder. Debugging these mismatches burned days. The ephemeral filesystem (outside the Volume mount) forced an S3 integration for uploaded target images, adding cross-platform complexity. Six months in, Railway works and costs roughly half of Render, but the agent DX friction from missing structured docs wasted more development time than the cost savings justified.

### Unknown Unknowns

1. **Railpack vs Nixpacks transition** — Railway migrated from Nixpacks to Railpack as the default builder. Older tutorials and community posts reference Nixpacks-specific config (`nixpacks.toml`) that doesn't apply to Railpack. Agents trained on older data surface wrong advice.
2. **`uv` without `uv.lock` edge case** — if the project uses `uv` without a lockfile present, Railway's auto-detection may not resolve dependencies correctly. A `pyproject.toml` is needed as fallback.
3. **`RAILPACK_DJANGO_APP_NAME` may need manual override** — non-standard WSGI module paths (e.g. `target_o_meter.wsgi:application` with underscores) can cause silent build failures if auto-detection misses them.
4. **Usage-based pricing unpredictability** — RAM at $10/GB/month and CPU at $20/vCPU/month means image processing spikes inflate the bill if workers don't sleep properly.
5. **Volume backup documentation gap** — Railway volumes support backups, but reliability and recovery procedures are less documented than Render's persistent disk offerings. The SQLite database file lives on this volume — backup procedures must be tested before going live.

## Operational Story

- **Preview deploys**: Railway creates preview environments for every PR via `railway up` in a linked branch. Each preview gets its own URL and isolated resources. No protection on preview URLs by default — add auth middleware if needed. Preview environments incur usage charges against the Hobby credit.
- **Secrets**: Environment variables set via `railway variable set KEY=VALUE`, CLI, or dashboard. Variables are encrypted at rest and injected at runtime. Scoped to the service level. Rotate by deleting and re-setting. No fine-grained access control — workspace members can read all variables.
- **Rollback**: `railway redeploy` triggers a new deployment from a previous commit. Redeploy from any past deployment in the dashboard or via CLI. Typical time-to-revert: 2-5 minutes (build + deploy). SQLite schema changes are embedded in the database file on the persistent Volume — migrations are applied on deploy and do NOT roll back automatically. If a migration is irreversible, the database must be restored from a Volume backup.
- **Approval**: Production deploy requires pushing to the main branch (or manual `railway up --prod`). An agent with a scoped `RAILWAY_TOKEN` can trigger deploys, set variables, and read logs. Destructive actions (drop database, delete service, delete project) should be dashboard-only by convention. No built-in approval gates at Hobby tier.
- **Logs**: `railway logs` streams runtime logs. `railway logs --build` streams build logs. MCP tools provide structured log access. Logs are ephemeral — no persistent log storage on Railway; aggregate to an external service (e.g. Axiom, Logtail) for retention.

## Risk Register

| Risk | Source | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| Real monthly cost exceeds $5 Hobby credit | Devil's advocate | M | M | Budget $8-12/month; monitor usage via `railway status` and dashboard alerts. SQLite eliminates managed DB costs. Upgrade to Pro ($20/mo with $20 credit) if usage grows. |
| Agent hallucinates Railway CLI commands due to missing llms.txt | Devil's advocate / Pre-mortem | M | M | Pin correct Railpack CLI commands in AGENTS.md. Reference Railway's GitHub docs repo directly when agent is uncertain. |
| OpenCV build failures or slow deploys on Railpack | Unknown unknowns | M | M | Pre-build a Docker image with OpenCV installed and use Dockerfile deploy path instead of Railpack auto-detection. |
| Cold-start 502 errors if serverless sleep mode is enabled | Devil's advocate | M | H | Keep serverless mode disabled for the web service; accept higher cost for better UX. Use a health-check ping service if sleep mode is required. |
| `RAILPACK_DJANGO_APP_NAME` not auto-detected for `target_o_meter` | Unknown unknowns | M | H | Set `RAILPACK_DJANGO_APP_NAME=target_o_meter` as an environment variable explicitly in Railway dashboard. |
| SQLite db.sqlite3 lost on redeploy without Volume mount | Research finding | H | H | Mount a Railway Volume and point `DATABASES['NAME']` to the volume path via `RAILWAY_VOLUME_MOUNT_PATH` env var. Test with a redeploy before going live. |
| Concurrent writes corrupt SQLite under load | Unknown unknowns | L | H | SQLite serialized mode via `PRAGMA journal_mode=WAL` in Django connection. At hobbyist scale (single user, <10 concurrent writes) this is safe. Monitor for `database is locked` errors. |
| Uploaded target images lost on redeploy (ephemeral filesystem) | Research finding | H | H | Use Railway Volumes for persistent storage or integrate Cloudflare R2 / AWS S3 for image uploads from day one. |
| Railpack vs Nixpacks confusion in agent suggestions | Unknown unknowns | M | L | Document the Railpack-specific config in AGENTS.md. Ignore any `nixpacks.toml` references. |
| Usage spike during concurrent image processing inflates bill | Unknown unknowns | M | M | Set resource limits on worker services. Cap concurrent image processing at 3 as per PRD. Monitor via Railway dashboard. |
| SQLite Volume backup untested — data loss on Volume failure | Unknown unknowns | M | H | Test Railway Volume backup/restore before going live. Add a periodic `sqlite3 db.sqlite3 ".backup /mnt/backup/db.sqlite3"` cron job if Volume backups are unreliable. Schedule regular local downloads of the SQLite file as a safety net. |

## Getting Started

1. **Install Railway CLI**:
   ```bash
   curl -fsSL cli.new | sh
   ```

2. **Login and create project**:
   ```bash
   railway login
   railway init
   ```

3. **Add a persistent Volume for SQLite**:
    ```bash
    railway volume add
    ```
    Mount the volume at `/data` in the service settings. This is where `db.sqlite3` will live — the container filesystem is ephemeral and loses data on every deploy.

4. **Configure Django for Railway**:
    - Set `RAILPACK_DJANGO_APP_NAME=target_o_meter` as a Railway variable.
    - Add `whitenoise`, `gunicorn` to dependencies.
    - The Django settings already use SQLite3 by default. Railway's `RAILWAY_VOLUME_MOUNT_PATH` env var is used to redirect the database path to the persistent Volume (see `settings.py`).
    - Add WhiteNoise middleware for static file serving.
    - Set `DISABLE_COLLECTSTATIC=1` if handling collectstatic manually.
    - **Do NOT add `dj-database-url` or `psycopg2`** — the app uses SQLite3, not PostgreSQL.
    - **Do NOT run `railway add --database postgres`** — there is no managed database. SQLite lives on the Volume.

5. **Deploy**:
   ```bash
   railway up
   ```
   Railway's Railpack builder auto-detects Django via `manage.py`, installs `uv` dependencies, runs migrations, and starts Gunicorn.

6. **Set up MCP for agent access** (optional):
   ```bash
   railway mcp install
   ```

## Out of Scope

The following were not evaluated in this research:
- Docker image configuration
- CI/CD pipeline setup (planned: GitHub Actions with auto-deploy on merge)
- Production-scale architecture (multi-region, HA, DR)
- Computer vision service deployment architecture (separate from Django web service)
