# `src/domains/identity/` — zero-email identity domain

The auth vertical's anchor: a swappable `User` model keyed by Auth0's
canonical `sub`, a role **derived** from `OWNER_SUB_ID` (never persisted), and
the pure services the BFF calls. Per AGENTS.md §5 this domain is pure Python
— **no `django-ninja`, no HTTP** (the BFF in `src/bff/` handles those).

## The core design: role is derived, not stored

```python
@property
def role(self) -> str:
    owner_sub = os.environ.get("OWNER_SUB_ID", "")
    if owner_sub and self.sub == owner_sub:
        return Role.OWNER
    return Role.USER
```

`OWNER_SUB_ID` is the **single source of truth**. A row never stores its role,
so hand-editing a row (or a stale DB) cannot desync Owner state from config.
Fail-closed: an empty/missing env var means *no one* is Owner.

## Required environment variables

All read in `src/target_o_meter/settings.py` via `os.environ.get(...)`. Copy
`.env.example` to `.env` for local dev. None are required for a DEBUG-mode
smoke (the dev-bypass makes Auth0 optional) — see "Debug mode" below.

### Identity / role

| Variable | Required? | Default | Purpose |
|---|---|---|---|
| `OWNER_SUB_ID` | **Prod: yes** | `""` | The `sub` that resolves to the Owner role. Empty → fails closed (no one is Owner); `target_o_meter.W001` warns in prod. |
| `DEV_AUTH_BYPASS_SUB` | Dev only | `""` | When set **and** `DEBUG=True`, the dev-bypass middleware auto-authenticates every request as this `sub`'s user. **Must be unset in prod** — `target_o_meter.E001` refuses to boot if it's set while `DEBUG=False`. Set it equal to `OWNER_SUB_ID` to impersonate Owner locally. |
| `DEV_ADMIN_SUB` | Dev only | `""` | The `sub` for the seeded dev admin (Django admin GUI login). |
| `DEV_ADMIN_NICK` | Dev only | `dev-admin` | Display nick for the dev admin. |
| `DEV_ADMIN_PASSWORD` | Dev only | `""` | Password for the dev admin (must be usable — admin login requires it; OAuth users always have unusable passwords). |

### Auth0 (optional in debug, required for the real OAuth flow)

| Variable | Required? | Default | Purpose |
|---|---|---|---|
| `AUTH0_CLIENT_ID` | Prod | `""` | Auth0 OIDC client ID. |
| `AUTH0_CLIENT_SECRET` | Prod | `""` | Auth0 OIDC client secret (confidential client). |
| `AUTH0_DOMAIN` | Prod | `""` | Auth0 tenant domain, e.g. `your-tenant.eu.auth0.com`. |

### Django core (read by `settings.py`, not this domain)

| Variable | Default | Notes |
|---|---|---|
| `DEBUG` | `True` | Must be `True` for the dev-bypass to fire. **`False` in prod** (E001 guards the bypass). |
| `SECURE_COOKIES` | `False` | Toggles `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE`. `False` in dev (no HTTPS on localhost); `True` in prod. |
| `SECRET_KEY` | insecure default | Set a real one for prod. |
| `ALLOWED_HOSTS` | `[]` | Comma-separated prod hosts. |

## Debug mode (bypass Auth0 connectivity)

Two things make local dev Auth0-free:

1. **`DevAuthBypassMiddleware`** (`src/target_o_meter/dev_auth_bypass.py`) —
   auto-authenticates every request as `DEV_AUTH_BYPASS_SUB`'s user when
   `DEBUG=True`. No Auth0 redirect, no token exchange.
2. **Derived role reuse** — the bypass reads the same `OWNER_SUB_ID` the role
   property does. Set the two env vars equal to act as Owner locally.

### Quick start

From the **repository root** (`./`):

```bash
# 1. Install all dependency groups (authlib, pytest-playwright, etc.)
uv sync --all-groups

# 2. Apply migrations (creates identity_user + Django internal tables)
uv run python src/manage.py migrate

# 3. (Optional) Seed a dev admin so you can log into /admin/ in debug mode
DEV_ADMIN_SUB="auth0|dev-admin-sub" \
DEV_ADMIN_NICK="dev-admin" \
DEV_ADMIN_PASSWORD="dev-admin-pass" \
uv run python src/manage.py shell -c "
import os
from django.contrib.auth import get_user_model
User = get_user_model()
sub = os.environ['DEV_ADMIN_SUB']
if not User.objects.filter(sub=sub).exists():
    User.objects.create_superuser(
        sub=sub, nick=os.environ['DEV_ADMIN_NICK'],
        password=os.environ['DEV_ADMIN_PASSWORD'],
    )
    print('seeded dev admin')
else:
    print('dev admin already exists')
"

# 4. Start the dev server — bypass active, Auth0 not contacted.
#    DEV_AUTH_BYPASS_SUB == OWNER_SUB_ID → you are the Owner.
DEBUG=True \
DEV_AUTH_BYPASS_SUB="auth0|dev-bypass" \
OWNER_SUB_ID="auth0|dev-bypass" \
uv run python src/manage.py runserver
```

The server is now at `http://localhost:8000`.

### Verifying the bypass works

```bash
# Auto-authenticated as the Owner (no Auth0 call):
curl http://localhost:8000/api/me
# → {"authenticated": true, "user": {"nick": "dev-auth0|de", "role": "owner"}}

# Owner-only route returns 200 (the 401/403 RBAC contract):
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/users
# → 200

# Main page renders nick + role:
curl -s http://localhost:8000/ | grep "logged in"
# → <p>logged in as dev-auth0|de (owner)</p>
```

### Impersonating User vs Owner

The only difference is whether `DEV_AUTH_BYPASS_SUB` equals `OWNER_SUB_ID`:

```bash
# As a plain User (different sub → role = user → /api/users is 403):
DEBUG=True \
DEV_AUTH_BYPASS_SUB="auth0|some-user" \
OWNER_SUB_ID="auth0|someone-else" \
uv run python src/manage.py runserver

# As the Owner (equal subs → role = owner → /api/users is 200):
DEBUG=True \
DEV_AUTH_BYPASS_SUB="auth0|the-owner" \
OWNER_SUB_ID="auth0|the-owner" \
uv run python src/manage.py runserver
```

### Django admin in debug mode

```bash
# The /admin/ GUI is reachable; log in with the seeded dev admin creds:
#   username (sub): auth0|dev-admin-sub
#   password:       dev-admin-pass
#
# identity_user rows are visible + searchable. role / is_owner / sub /
# last_login are read-only (role is derived from OWNER_SUB_ID — never editable).
```

### What you're NOT doing in debug mode

- **No Auth0 call** — the entire `/bff/login` → Auth0 → `/bff/callback` chain
  is unused. The real OAuth flow needs `AUTH0_*` env vars set and is covered by
  the deferred UAT test (`tests/acceptance/`, gated on `RUN_UAT=1`).
- **No token storage** — the bypass sets `request.user` directly; no session
  token blob is written (minimizes leakage surface, per the research).
- **No production guards fired** — `E001` / `W001` are inert under `DEBUG=True`
  (they gate prod-shaped configs only).

## Python API (the BFF's seam)

These services take primitives and return DTOs — the BFF calls them; nothing
imports ORM objects across the boundary (AGENTS.md §5).

```python
from src.domains.identity.services import (
    get_or_create_user_by_sub,  # OAuth callback: resolve-or-create by sub
    get_user_context,           # read accessor → UserContextDTO
    is_owner,                   # thin read of dto.is_owner
    list_users,                 # owner route: all users as UserOut (no sub)
)

# After Auth0 callback validates the token:
dto = get_or_create_user_by_sub("auth0|canonical-sub")
# dto.user_uuid, dto.sub, dto.nick, dto.role, dto.is_owner
```

## Tests

```bash
# Identity domain unit tests (derived role, nick CI-uniqueness, get_or_create)
uv run pytest src/domains/identity/tests/

# System tests exercising the BFF → identity seam (401/403/200 RBAC contract)
uv run pytest tests/system/test_auth_flow.py tests/system/test_dev_bypass.py
```

Tests use `test_utils.py` seeders (`make_user`, `make_owner`) — never ORM tools
directly against the model (AGENTS.md §5, Test Encapsulation).

## Layout

```
src/domains/identity/
├── __init__.py
├── apps.py                AppConfig (label = "identity")
├── models.py              User (AbstractBaseUser + PermissionsMixin),
│                          UserManager, Role, _generated_nick
├── ports.py               Protocol boundaries (reserved — empty in F-01)
├── dtos.py                UserContextDTO / UserOut / MeOut (no sub crosses out)
├── services.py            get_or_create_user_by_sub / get_user_context /
│                          is_owner / list_users
├── test_utils.py          make_user / make_owner seeders
├── admin.py               read-mostly UserAdmin (role/is_owner read-only)
├── migrations/
│   └── 0001_initial.py    creates identity_user + the nick-CI-unique constraint
└── tests/
    ├── conftest.py        owner_sub / user_sub / seeded_user fixtures
    └── test_services.py   derived role, nick CI-uniqueness, get_or_create
```

## Production-safety guards (enforced by `src/target_o_meter/checks.py`)

| Check | Severity | Fires when | Effect |
|---|---|---|---|
| `target_o_meter.E001` | **Error (boot-block)** | `DEV_AUTH_BYPASS_SUB` set + `DEBUG=False` | `manage.py check` exits non-zero; app refuses to start. |
| `target_o_meter.W001` | Warning | `OWNER_SUB_ID` empty + `DEBUG=False` | App boots; Owner role is inert until configured. |

These run at `manage.py check` time (plain `@register(Tags.security)`, **not**
`deploy=True`) so they fire on every Django command, not just `--deploy`. The
dev-bypass middleware **also** self-gates on `DEBUG=False` as a serving-layer
backstop (belt-and-suspenders with E001).

## References

- Plan: `context/changes/oauth-roles-scaffold/plan.md`
- Research (Q1–Q8 resolved): `context/changes/oauth-roles-scaffold/research.md`
- Architecture rules: `AGENTS.md` §2 (Identity & Roles), §5 (boundaries)
- DDD precedent: `src/domains/vision/` (pure services, DTOs, UUID PKs)
