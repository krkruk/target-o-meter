# Deploy Plan: Target-o-meter → Railway

- **Project**: Target-o-meter
- **Platform**: Railway (Hobby plan, $5/month + usage), **region: EU West Metal — Amsterdam, Netherlands (`europe-west4-drams3a`)**
- **Stack**: Django 6.0.5, Python 3.14, SQLite3 on Railway Volume, uv
- **Recommended by**: `/10x-infra-research` (see `context/foundation/infrastructure.md`)
- **Date**: 2026-05-26

## Context

This plan covers the initial deployment of the Target-o-meter Django app to Railway. It also defines the workflow for subsequent rollouts. The app uses SQLite3 on a persistent Railway Volume (no managed database). Railpack auto-detects Django via `manage.py` and runs migrations + gunicorn on every deploy.

**Pre-conditions verified:**

- `railway` CLI v4.64.0 authenticated (krzysztof.pawel.kruk@gmail.com)
- `gh` CLI v2.92.0 authenticated (account: krkruk, SSH protocol)
- Django 6.0.5 project scaffold exists with `manage.py` and `target_o_meter/settings.py`
- `settings.py` already reads `RAILWAY_VOLUME_MOUNT_PATH` for SQLite DB path (line 79)
- No git remote configured yet
- No `pyproject.toml`, `uv.lock`, or `requirements.txt` exist yet
- `gunicorn` and `whitenoise` are NOT yet installed

---

## Phase 1 — Code Preparation (local)

Prepare the codebase so Railpack can build and deploy it correctly.

- [ ] **1.1** Add `gunicorn` and `whitenoise` dependencies:
  ```bash
  uv add gunicorn whitenoise
  ```
  This creates/updates `pyproject.toml` and `uv.lock` (Railpack needs both for `uv`-based projects).

- [ ] **1.2** Verify `pyproject.toml` was created and lists `django`, `gunicorn`, `whitenoise` under dependencies.

- [ ] **1.3** Verify `uv.lock` was generated.

- [ ] **1.4** Update `target_o_meter/settings.py` for production:
  - Add `whitenoise.middleware.WhiteNoiseMiddleware` to `MIDDLEWARE` right after `django.middleware.security.SecurityMiddleware`
  - Set `STATIC_ROOT = BASE_DIR / "staticfiles"`
  - Make `SECRET_KEY` environment-aware:
    ```python
    SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-0n%b*1&a_*5va-)s1tv8e+98yzsb=o*f!7w%h#puwwsjz6dlq6")
    ```
  - Make `DEBUG` environment-aware:
    ```python
    DEBUG = os.environ.get("DEBUG", "True").lower() == "true"
    ```
  - Make `ALLOWED_HOSTS` environment-aware:
    ```python
    ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "").split(",") if os.environ.get("ALLOWED_HOSTS") else []
    ```

- [ ] **1.5** Confirm `DATABASES` config already uses `RAILWAY_VOLUME_MOUNT_PATH` (already in place at line 79):
  ```python
  DATABASES = {
      'default': {
          'ENGINE': 'django.db.backends.sqlite3',
          'NAME': Path(os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', BASE_DIR)) / 'db.sqlite3',
      }
  }
  ```

- [ ] **1.6** Create `.python-version` with content `3.14` (matches local runtime; Railpack reads this file).

- [ ] **1.7** Add `staticfiles/` to `.gitignore`.

- [ ] **1.8** Run system check:
  ```bash
  uv run python manage.py check --deploy
  ```
  Address any warnings related to `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, or security settings.

- [ ] **1.9** Run migrations locally to verify nothing is broken:
  ```bash
  uv run python manage.py migrate
  ```

---

## Phase 2 — GitHub Repository

Create the remote repository and push the initial code.

- [ ] **2.1** Create a public GitHub repo and push:
  ```bash
  gh repo create target-o-meter --public --source=. --push
  ```

- [ ] **2.2** Verify remote is configured:
  ```bash
  git remote -v
  ```
  Expected output: `origin` pointing to `github.com:krkruk/target-o-meter.git` (SSH).

---

## Phase 3 — Railway Project & Service

Create the Railway project and link it to the GitHub repo.

- [ ] **3.1** Initialize Railway project:
  ```bash
  railway init
  ```
  Name it `target-o-meter`. **Set the project region to EU West Metal (Amsterdam, `europe-west4-drams3a`)** via the Railway dashboard → Project Settings → Region, or via CLI if region selection is prompted during init.

- [ ] **3.2** Link the service to the GitHub repo. In the Railway dashboard:
  - Open the project → click the service → Settings → Source
  - Connect the `krkruk/target-o-meter` repo
  - Set deploy branch to `main`

- [ ] **3.3** Verify the service is linked (dashboard should show "Connected to GitHub").

---

## Phase 4 — Railway Volume (SQLite Persistence)

The container filesystem is ephemeral. SQLite must live on a persistent Volume to survive deploys.

- [ ] **4.1** Create a Volume attached to the web service:
  - Via Railway dashboard: right-click service → Add Volume
  - Set mount path to `/data`
  - Size: 1 GB (sufficient for MVP SQLite database)

- [ ] **4.2** Verify the volume is attached to the web service in the dashboard.

- [ ] **4.3** Verify `RAILWAY_VOLUME_MOUNT_PATH` is auto-injected as an environment variable (Railway does this automatically when a volume is attached — value will be `/data`).

---

## Phase 5 — Railway Environment Variables

Set all required environment variables on the Railway service.

- [ ] **5.1** Set `RAILPACK_DJANGO_APP_NAME=target_o_meter`
  ```bash
  railway variable set RAILPACK_DJANGO_APP_NAME=target_o_meter
  ```
  This ensures Railpack finds `target_o_meter.wsgi:application` (the underscored module name may not auto-detect correctly).

- [ ] **5.2** Generate and set `SECRET_KEY`:
  ```bash
  python3 -c "import secrets; print(secrets.token_urlsafe(50))"
  ```
  Then:
  ```bash
  railway variable set SECRET_KEY=<generated-key>
  ```

- [ ] **5.3** Set `ALLOWED_HOSTS` to the Railway-provided domain:
  ```bash
  railway variable set ALLOWED_HOSTS=<service-name>.up.railway.app
  ```
  Replace `<service-name>` with the actual domain shown in the Railway dashboard. Include any custom domain if configured later.

- [ ] **5.4** Set `DEBUG=False`:
  ```bash
  railway variable set DEBUG=False
  ```

- [ ] **5.5** Confirm these variables are set:
  ```bash
  railway variable
  ```

**Do NOT set:**
- `DISABLE_COLLECTSTATIC` — let Railpack run `collectstatic` automatically
- `DATABASE_URL` — not using PostgreSQL
- `PGHOST`, `PGUSER`, etc. — not using PostgreSQL

---

## Phase 6 — Initial Deployment

Trigger the first build and deploy.

- [ ] **6.1** Trigger deployment (pick one):
  - **Via CLI**: `railway up`
  - **Via GitHub**: push a commit to `main` (if GitHub integration is wired in Phase 3)

- [ ] **6.2** Monitor build logs:
  ```bash
  railway logs --build
  ```
  Watch for:
  - Railpack detecting Python/Django
  - `uv` installing dependencies from `uv.lock`
  - `python manage.py collectstatic` completing
  - Image build succeeding

- [ ] **6.3** Monitor runtime logs:
  ```bash
  railway logs
  ```
  Watch for:
  - `python manage.py migrate` running successfully (Railpack runs this as part of the start command)
  - `gunicorn target_o_meter.wsgi:application` starting
  - No import errors or module-not-found errors

- [ ] **6.4** Note the deployment URL from the Railway dashboard or `railway domain` output.

---

## Phase 7 — Post-Deploy Verification

Confirm the deployment is healthy.

- [ ] **7.1** Visit the Railway URL in a browser. Expected: Django welcome page (no apps installed yet) or the app's landing page.

- [ ] **7.2** Verify static files are served correctly (WhiteNoise). Check browser dev tools network tab — static files should return 200, not 404.

- [ ] **7.3** Verify migrations applied:
  ```bash
  railway run python manage.py showmigrations
  ```
  All migrations should show `[X]`.

- [ ] **7.4** Verify SQLite database exists on the Volume:
  ```bash
  railway run ls -la /data/
  ```
  Should show `db.sqlite3`.

- [ ] **7.5** Check for errors:
  ```bash
  railway logs
  ```
  No 500, 502, or import errors.

- [ ] **7.6** Verify the app responds to a health check:
  ```bash
  curl -s -o /dev/null -w "%{http_code}" https://<service-name>.up.railway.app/
  ```
  Expected: `200` or `404` (404 is acceptable if no URLs are configured yet — the Django default page returns 200).

---

## Phase 8 — Subsequent Rollouts

After the initial deployment, new deploys follow this workflow:

### Automatic deploys (recommended)

1. Make changes locally, commit, push to `main`
2. Railway auto-detects the push and triggers a new deploy
3. Railpack rebuilds: installs deps → `collectstatic` → `migrate` → `gunicorn`
4. Monitor via `railway logs --build` and `railway logs`

### Manual CLI deploys

```bash
railway up
```

### Rollback

```bash
railway redeploy
```
Select a previous successful deployment. Both the image and variables are restored.

### What to check after every deploy

- `railway logs` — no crashes or 500 errors
- Visit the app URL — smoke test
- `railway run python manage.py showmigrations` — if migrations were added

---

## Risk Mitigations Applied

| Risk | Mitigation in this plan |
|---|---|
| `RAILPACK_DJANGO_APP_NAME` not auto-detected for `target_o_meter` | Explicitly set in Phase 5.1 |
| SQLite db lost on redeploy | Volume mount at `/data` + `RAILWAY_VOLUME_MOUNT_PATH` in settings (Phase 4) |
| `uv` without `pyproject.toml`/`uv.lock` | Phase 1 creates both via `uv add` |
| Static files 404 in production | WhiteNoise middleware + `STATIC_ROOT` configured in Phase 1.4 |
| Secret key leaked in source | Environment-aware `SECRET_KEY` in Phase 1.4, real key set in Phase 5.2 |
| Cold-start 502 on sleep mode | Keep serverless mode disabled (Hobby plan default for web services) |

## Not in Scope (future work)

- CI/CD pipeline (GitHub Actions auto-deploy on merge)
- Custom domain configuration
- SSL certificate management (Railway provides automatic HTTPS)
- Computer vision service deployment
- S3 / object storage for uploaded target images
- Monitoring / alerting setup
- Database backup automation
