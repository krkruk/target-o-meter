---
date: 2026-07-24T20:30:00+02:00
researcher: ZCode (via /10x-research)
git_commit: eebc2f8db8260109abf1dc036d933ab352054410
branch: feature/oauth
repository: krkruk/target-o-meter
topic: "F-01 (expanded): Auth0 OIDC BFF scaffold + custom identity model + two-tier RBAC + welcome/login/main flow"
tags: [research, codebase, identity, auth, oauth, auth0, rbac, bff, django-ninja, react]
status: complete
last_updated: 2026-07-24
last_updated_by: ZCode
last_updated_note: "Resolved Q6–Q8: (Q6) env var = DEV_AUTH_BYPASS_SUB; (Q7) DEFER the UAT Playwright test to a later slice — F-01 ships only the DEV/UAT scaffolding; (Q8) when the UAT test lands, parameterize across User + Owner creds."
---

# Research: OAuth + roles scaffold (F-01, expanded scope)

**Date**: 2026-07-24
**Researcher**: ZCode (via `/10x-research oauth-roles-scaffold`)
**Git Commit**: `eebc2f8`
**Branch**: `feature/oauth`
**Repository**: `krkruk/target-o-meter`

## Research Question

F-01 was scoped as a minimal auth enabler (OAuth wiring + role flag + owner-by-env). The user expanded it to cover the full auth vertical: Auth0 sign-in, a custom identity model storing only `sub` + unique `nick` + derived role, a welcome page, a login entry, a post-login redirect to an authenticated main page, and an RBAC enforcement skeleton for Owner / User.

Two assumptions changed during this conversation vs. the original foundation docs:
1. **IdP**: roadmap/shape-notes assumed Google directly. The user mandates **Auth0** (which can federate Google/Microsoft/GitHub + offer passwordless email as connections).
2. **Passwordless only**: social login (Google / Microsoft / GitHub) **and/or** magic link — no username+password flow, to minimize the data-leakage surface on the Auth0 side.

## Summary

The architecture is effectively pre-decided by PRD §Identity & Roles + AGENTS.md §2: *"Sessions: Managed via Django encrypted HttpOnly cookies (BFF pattern)."* This mandates the **BFF / OIDC Authorization Code + PKCE** flow (Django-mediated, Authlib, tokens server-side, browser carries only Django's `sessionid`). It is incompatible with the "React holds a JWT in localStorage" SPA pattern. The [official Auth0 Django quickstart](https://auth0.com/docs/quickstart/webapp/django) implements exactly this flow; our addition is PKCE for defense-in-depth.

Decisions reached in the discovery conversation:

| Decision | Outcome |
|---|---|
| Auth flow | BFF / OIDC Authorization Code + **PKCE** (Authlib `code_challenge_method="S256"`) |
| Token storage | Server-side Django session only; `sessionid` HttpOnly cookie to browser |
| IdP | **Auth0** (replaces the Google assumption) |
| Login methods | **Passwordless only** — social (Google/Microsoft/GitHub) and/or magic-link email; Auth0 Database connection disabled |
| RBAC | Two-tier only: Owner + User (the three-tier Superowner/Owner/User idea was raised and **dropped**) |
| Owner resolution | `sub == OWNER_SUB_ID` env var at login/read — single source of truth |
| Identity model | Custom swappable `AbstractBaseUser` in `src/domains/identity/models.py`, fields: `sub` (unique), `nick` (case-insensitive unique), derived `role` |
| Scope | **Expanded F-01** — auth wiring + identity model + welcome/login/main flow + RBAC enforcement skeleton. S-01 dashboard content and S-04 owner actions deferred. |

**All open questions resolved (2026-07-24).** The previously-blocking Q1 (Auth0 cross-connection `sub` divergence — the same human logging in via Google and via magic-link would create two rows) is settled with **Auth0 account linking (user-initiated only)**, which gives a stable canonical `sub`. See §Open Questions Q1–Q5 for the remaining decisions: Django templates now (React in S-01), keep `last_login`, add an `OWNER_SUB_ID`-empty startup guard, and add `authlib` to `pyproject.toml` default group. The research is ready to hand to `/10x-plan`.

Two cross-agent **synthesis decisions** (I resolved these to remove ambiguity in the plan):
- **Session key strategy** (§Architecture Insights): the auth-domain agent stores the full token blob as `request.session["user"]`; the RBAC agent reads `request.session["sub"]`. These are incompatible. **Resolution**: BFF callback stores `request.session["user_uuid"]` (the resolved Django user PK) and `login(request, user)` writes `_auth_user_id`, so `request.user` is populated by `AuthenticationMiddleware`. The token blob is **not** stored long-term (minimize leakage, per the user's stated goal); `sub` is re-derivable from `request.user.sub` if ever needed.
- **Owner-source wording inconsistency**: `roadmap.md:64` says "owner determinable via a configured designated **email**"; `AGENTS.md:21` + `change.md:12` say `OWNER_SUB_ID` (`sub`). The email phrasing is **internally inconsistent** with AGENTS.md §2 "Zero Email Storage." `OWNER_SUB_ID` is the only value compatible with the zero-email rule. The roadmap line should be corrected in this change's plan.

## Detailed Findings

### 1. Auth0 + Authlib Django BFF integration (Authorization Code + PKCE)

The [Auth0 Django quickstart](https://auth0.com/docs/quickstart/webapp/django) + [GitHub sample](https://github.com/auth0-samples/auth0-django-web-app) are the canonical reference. The integration is thin — Authlib does the heavy lifting.

**Authlib registration** (PKCE added beyond the tutorial — ~3 lines via `code_challenge_method`):

```python
from authlib.integrations.django_client import OAuth

oauth = OAuth()
oauth.register(
    "auth0",
    client_id=settings.AUTH0_CLIENT_ID,
    client_secret=settings.AUTH0_CLIENT_SECRET,
    client_kwargs={
        "scope": "openid profile",          # NO email scope (see §3)
        "code_challenge_method": "S256",     # PKCE (defense-in-depth)
    },
    server_metadata_url=f"https://{settings.AUTH0_DOMAIN}/.well-known/openid-configuration",
)
```

- Auth0 accepts PKCE on the **confidential** client (one holding `client_secret`); sending `code_challenge` alongside `client_secret` is well-formed and recommended ([Auth0 Authorization Code + PKCE](https://auth0.com/docs/get-started/authentication-and-authorization-flow/authorization-code-flow-with-pkce)). Authlib auto-generates and replays the `code_verifier`; the app never touches it manually.
- OAuth/OIDC views (login, callback, logout) live in **`src/bff/`** per AGENTS.md §5 ("ONLY `src/bff/` is permitted to import django-ninja or handle HTTP"). The identity domain stays pure Python.

### 2. Passwordless on Auth0 for a regular web app

**Key insight: passwordless is NOT a different flow for the backend.** It is configured entirely on the Auth0 side and reached through the **same** `/authorize` redirect. ([Passwordless overview](https://auth0.com/docs/authenticate/passwordless), [Passwordless with Universal Login](https://auth0.com/docs/authenticate/passwordless/passwordless-with-universal-login))

- The Django `login` view redirects to Auth0's `/authorize` exactly as in the social case.
- Universal Login renders the passwordless-email prompt OR the social buttons (Google/MS/GitHub).
- Auth0 emails a magic link; clicking it returns to Auth0, which completes the Authorization Code exchange and redirects to our `/callback` with a `code` — **identical to social login**.
- Our `callback` runs `authorize_access_token` as normal.

**Two layers enforce passwordless-only:**
1. **Dashboard gate (hard)**: Application → Settings → Connections — enable only Google / Microsoft / GitHub + the email (passwordless) connection; **disable the Database connection**. ([dev.to walkthrough](https://dev.to/kleeut/setting-up-email-passwordless-authentication-with-auth0-28k3))
2. **Per-request gate (optional)**: pin `connection="email"` on a dedicated "sign in with email link" button via `authorize_redirect(..., connection="email")`. The generic login button leaves `connection` unset so Universal Login shows all enabled connections.

**Does Django need to know which connection was used? No.** With Universal Login the app receives a normalized OIDC response; `sub` is the only identifier the app needs. The `sub` *value* encodes the connection as a prefix (`google-oauth2|…`, `github|…`, `email|…`), so the app *can* infer the provider if ever useful but doesn't need to.

### 3. Scoping claims — `openid profile` (NO email) is the enforcement mechanism

| Scope | Claims returned |
|---|---|
| `openid` (mandatory) | **`sub`** (always present) |
| `profile` | `name`, `nickname`, `picture`, … |
| `email` | `email`, `email_verified` |

- **Dropping `email` from scope prevents the email from being *returned to our app*.** With `scope="openid profile"`, `email`/`email_verified` are simply absent from both the id_token and the userinfo response. This is genuine defense-in-depth — we don't rely on discipline to "not store" it; Auth0 never sends it. ([OIDC scopes](https://auth0.com/docs/get-started/apis/scopes/openid-connect-scopes))
- **Caveat (relevant to the "minimal Auth0 footprint" goal)**: for passwordless *email* users, Auth0 obviously knows the email (it's how the magic link is delivered). Dropping the scope only stops the email from reaching us; it does not stop Auth0 from holding it. The social paths avoid even that. **Trade-off to surface to the user**: if true minimalism matters, prefer social; if UX matters, accept the Auth0-side email for magic-link users.

### 4. ID token validation — Authlib does it automatically

When `oauth.auth0.authorize_access_token(request)` is called **and** `server_metadata_url` is set, Authlib automatically validates: **signature** (via JWKS), **`iss`**, **`aud`**, **`nonce`** (round-tripped through the session), and **`exp`**. ([Authlib Web Clients — Parsing id_token](https://docs.authlib.org/en/stable/oauth2/client/web/index.html), corroborated by [SO analysis](https://stackoverflow.com/questions/67637303/does-authorize-access-token-also-verify-an-id-token))

The app must NOT hand-roll JWT verification (it would weaken the check). The only manual concern: Authlib validates `exp` at callback time, not continuously — `SESSION_COOKIE_AGE` bounds the exposure (§5).

### 5. Session cookie hardening + the SameSite=Lax trap

```python
# src/target_o_meter/settings.py  (production values gated on env)
SESSION_COOKIE_HTTPONLY = True                  # default; keep
SESSION_COOKIE_SECURE   = os.environ.get("SECURE_COOKIES", "False").lower() == "true"  # HTTPS on Render
SESSION_COOKIE_SAMESITE = "Lax"                 # DO NOT change to Strict (see below)
SESSION_COOKIE_AGE      = 60 * 60 * 8           # 8h — bound token lifetime exposure
CSRF_COOKIE_SAMESITE    = "Lax"
CSRF_COOKIE_SECURE      = os.environ.get("SECURE_COOKIES", "False").lower() == "true"
CSRF_COOKIE_HTTPONLY    = False                 # SPA must read csrftoken for POSTs
```

**The `SameSite=Lax` trap (critical):** the OAuth callback is a top-level GET navigation originating from a different site (Auth0). With `Lax`, the `sessionid` cookie **is** sent on that cross-site redirect, so `authorize_access_token` can read the nonce Authlib stored pre-redirect. With **`Strict`**, the cookie is NOT sent → Authlib finds no nonce → **validation fails / login breaks silently**. ([Django settings](https://docs.djangoproject.com/en/6.0/ref/settings/), [mozilla-django-oidc #497](https://github.com/mozilla/mozilla-django-oidc/issues/497)) — add a code comment + a smoke test; this is a documented real-world OIDC failure mode.

### 6. Logout (RP-initiated) — terminate both sessions

The Auth0 logout endpoint is `GET https://{AUTH0_DOMAIN}/v2/logout`. With `client_id` present, `returnTo` is validated against the **application's** Allowed Logout URLs. ([How Logout Works](https://auth0.com/docs/authenticate/login/logout), [Redirect Users After Logout](https://auth0.com/docs/authenticate/login/logout/redirect-users-after-logout))

```python
def logout(request):
    request.session.clear()                # kill Django session
    return redirect(
        f"https://{settings.AUTH0_DOMAIN}/v2/logout?"
        + urlencode({"returnTo": request.build_absolute_uri(reverse("index")),
                     "client_id": settings.AUTH0_CLIENT_ID}, quote_via=quote_plus)
    )
```

- Add the welcome-page URL (e.g. `https://target-o-meter.onrender.com/`) + dev `http://localhost:8000/` to **Allowed Logout URLs** (application Settings).
- `/v2/logout` terminates the Auth0 SSO session but NOT the upstream social IdP session (Google/GitHub) unless `federated` is passed — social providers handle federated logout inconsistently, so non-federated is sufficient and expected.
- Gotcha: mis-encoding `returnTo` makes Auth0 silently fall back to the first allowed URL. The sample's `urlencode(..., quote_via=quote_plus)` is the correct fix.

### 7. Identity model — custom swappable `AbstractBaseUser`

**Decision: `AbstractBaseUser` + `BaseUserManager`, no `PermissionsMixin`.** Reasoning:
- `AbstractUser` is disqualified (drags in `username`, `email`, `first_name`, `last_name`, `is_staff`, `date_joined` — violates Zero Email Storage).
- `PermissionsMixin` is disqualified (Owner/User is a flat flag, not Django's group/permission matrix; the mixin adds `is_superuser` + 3 extra tables that overlap confusingly with the derived Owner role).
- `AbstractBaseUser` is the minimal base that still plugs into `AuthenticationMiddleware` + `request.user` + `login()`/`logout()` + `pytest-django`'s `client.login()`/`force_login()`.

**Mid-project `AUTH_USER_MODEL` swap is safe here** because the project has **no real user data** (`auth_user` table is empty). Clean-slate path: set `AUTH_USER_MODEL = "identity.User"` in settings **before** `makemigrations identity`, then `rm db.sqlite3 && migrate`. ([Django #25313](https://code.djangoproject.com/ticket/25313)) Do NOT use the data-preserving two-stage migration trick — there's nothing to preserve and it's more fragile.

**Recommended model** (`src/domains/identity/models.py`):

```python
from __future__ import annotations
import os, uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.db import models

class Role(models.TextChoices):
    OWNER = "owner", "Owner"
    USER  = "user",  "User"

class UserManager(BaseUserManager):
    use_in_migrations = True
    def create_user(self, sub: str, nick: str = "", **extra) -> "User":
        if not sub:
            raise ValueError("sub must be set")
        user = self.model(sub=sub, nick=nick or _generated_nick(), **extra)
        user.set_unusable_password()
        user.save(using=self._db)
        return user
    def create_superuser(self, sub: str, nick: str = "", **extra) -> "User":
        return self.create_user(sub=sub, nick=nick, **extra)  # Owner is NOT conferred here — derived from OWNER_SUB_ID

def _generated_nick() -> str:  # F-01 fallback before S-01 nick-prompt UX exists
    return f"shooter-{uuid.uuid4().hex[:8]}"

class User(AbstractBaseUser):
    id   = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sub  = models.CharField(max_length=255, unique=True)
    nick = models.CharField(max_length=64)           # CI uniqueness via Meta.constraints

    USERNAME_FIELD = "sub"
    REQUIRED_FIELDS: list[str] = []
    objects = UserManager()

    class Meta:
        app_label = "identity"
        db_table  = "identity_user"
        constraints = [
            models.UniqueConstraint(
                models.Lower("nick"), name="identity_user_nick_ci_unique",
                violation_error_message="That nick is already taken.",
            ),
        ]

    @property
    def role(self) -> str:                            # DERIVED, not persisted — env var is single source of truth
        owner_sub = os.environ.get("OWNER_SUB_ID", "")
        return Role.OWNER if owner_sub and self.sub == owner_sub else Role.USER

    @property
    def is_owner(self) -> bool:
        return self.role == Role.OWNER
```

**Design notes:**
- **Role is computed, not persisted.** Persisting a role column introduces a second source of truth that drifts when the env var changes or a row is hand-edited. Computing on read makes `OWNER_SUB_ID` authoritative — exactly what the PRD rule says. Cost: one string comparison per request against a value already on `request.user`.
- **`nick` case-insensitive uniqueness** via `UniqueConstraint(Lower("nick"))` — DB-portable (works on SQLite today and a future Postgres), better than per-column `NOCASE` collation (ASCII-only) or a plain `unique=True` (case-sensitive, lets "Bob"/"bob" coexist). Set `violation_error_message` explicitly because expression-based constraints don't get a field-specific default. ([Django constraints](https://docs.djangoproject.com/en/6.0/ref/models/constraints/))
- **`password` is always an unusable sentinel** (`set_unusable_password()`). It's a constant marker, not user data; required by Django's schema but never participates in login. **No identity leak.**
- **`last_login`** (nullable DateTime from `AbstractBaseUser`) is a timestamp, not identity — acceptable per the brief. It triggers a DB UPDATE per login; negligible at MVP scale.
- **One-class-per-file rule** (lessons.md): `UserManager` + `Role` are explicitly allowed by the lesson's carve-out ("supporting module-level constants and private helpers that serve *only* that class"). Keep `User` in `models.py` (not `models/user.py`) — Django scans `models.py` for the swappable model, and `vision/models.py` is the house-style precedent (`ScoringJob` alone in `models.py`).

### 8. BFF login flow — how `request.user` gets populated (no password)

1. Auth0 callback view calls `identity.services.get_or_create_user_by_sub(sub)` (pure-Python domain service wrapping `User.objects.get_or_create(sub=..., defaults={"nick": _generated_nick()})`).
2. BFF then calls `django.contrib.auth.login(request, user)`, which writes `_auth_user_id` to the session → `AuthenticationMiddleware` populates `request.user` on subsequent requests.
3. Because the BFF **skips `authenticate()`** (nothing to check a password against — Auth0 already proved identity), set `user.backend = "django.contrib.auth.backends.ModelBackend"` explicitly before `login()` (or rely on single-backend fallback; explicit is safer if a second backend is ever added). ([Django auth in views](https://docs.djangoproject.com/en/6.0/topics/auth/default/))
4. **Session-key synthesis decision** (resolved): store only `user_uuid` (or let `login()` write `_auth_user_id`); do **not** store the full token blob long-term (minimizes leakage, per user's stated goal). The token is needed only at callback time for `sub` extraction + validation; afterwards `request.user.sub` re-derives it.

### 9. RBAC enforcement in django-ninja

django-ninja's `SessionAuth` reads Django's session cookie and resolves `request.user` — already populated by `AuthenticationMiddleware`. **CSRF must be explicitly ON** for cookie-session auth: `NinjaAPI(csrf=True)`. ([django-ninja auth](https://django-ninja.dev/guides/authentication/), [django-ninja CSRF](https://django-ninja.dev/reference/csrf/))

```python
from ninja import Router, Schema
from ninja.security import SessionAuth
from ninja.errors import HttpError
from src.domains.identity.services import get_user_context, is_owner

session_auth = SessionAuth()
router = Router()

def require_owner(request):                # 403 path (authenticated-but-not-owner)
    dto = get_user_context(str(request.user.sub))
    if not is_owner(dto):
        raise HttpError(403, "Owner privileges required")
    return dto

@router.get("/me", auth=session_auth)       # any authenticated user (401 if anon)
def me(request): ...

@router.get("/users", auth=[session_auth, require_owner])  # owner-only
def list_all_users(request): ...
```

**401 vs 403 SPA contract** (confirmed by docs): a falsy return from an `auth` callable → **401** (SPA redirects to login); `HttpError(403)` inside a dependency → **403** (SPA shows Forbidden).

### 10. SPA auth-state bootstrap (the `/api/me` pattern)

Because `sessionid` is HttpOnly, JS cannot read it. Standard solution: a GET `/api/me` endpoint returning `{authenticated, user: {nick, role}}` or 401, called once on SPA mount.

```python
@router.get("/me", auth=session_auth, response={200: MeOut, 401: MeOut})
def me(request):
    dto = get_user_context(str(request.user.sub))
    return 200, MeOut(authenticated=True,
                      user=UserOut(nick=dto.nick, role="owner" if dto.is_owner else "user"))
```

- **GET `/api/me` needs no CSRF token** (GET is exempt). CSRF matters for future mutations (POST upload in S-02); the SPA obtains `csrftoken` via an `@ensure_csrf_cookie`-decorated GET and sends it back as `X-CSRFTOKEN`.
- SPA `fetch` uses `credentials: "same-origin"` (default) — correct because django-vite serves SPA + API same-origin.

### 11. Welcome / Login / Main — three states + redirect chain

**Redirect chain from the SPA's perspective** (SPA never sees tokens — it only observes "I'm on `/` and `/api/me` now returns 200"):
1. User clicks **Login** in React → `window.location.href = "/bff/login?next=" + encodeURIComponent(currentPath)`.
2. BFF `/bff/login` stashes `next` (server-side allowlist to avoid open-redirect) and 302s to Auth0 `/authorize`.
3. Auth0 authenticates → redirects to BFF `/bff/callback`.
4. Callback validates, creates Django session, 302s to `next` (default `/`).
5. Browser lands on `/` with `sessionid` cookie; SPA mounts, calls `/api/me` → 200.

**React routing** (three states): `<RequireAuth>` guard wrapping `/app`; unauthenticated `/` shows `<Welcome>`; login is a button → `/bff/login`. F-01 ships only the shell text "logged in as {nick} ({role})"; dashboard content is deferred.

### 12. django-vite + scope recommendation (React now vs. template now)

django-vite serves the SPA via template tags (`{% vite_hmr_client %}`, `{% vite_react_refresh %}`, `{% vite_asset '<entry>' %}`) inside a **Django template** (the SPA entry HTML is a Django template, not a standalone `index.html`). URL ordering matters: BFF/auth/api routes first, SPA catch-all last. ([django-vite](https://github.com/MrBin99/django-vite))

**Scope recommendation flagged for the user's decision:** React/Vite is **entirely absent** (`src/frontend/` is empty; roadmap baseline says "Frontend: absent"). Scaffolding React + Vite + React Router + the `/api/me` fetch layer + three-state routing + CSRF wrapper is a meaningful chunk. The URL contract (`/`, `/bff/login`, `/bff/callback`, `/api/me`) is **identical either way**. Two options:
- **(A) Django template for the main page in F-01, defer React to S-01.** Validates the full auth + RBAC chain end-to-end with minimal moving parts.
- **(B) Scaffold minimal React in F-01** (django-vite + a single `main.jsx` that fetches `/api/me` and renders the identity shell). Drags in the Vite build pipeline now.

Either way, the load-bearing RBAC + session + identity work is identical.

### 13. RBAC privilege matrix (draft)

| Action | User | Owner | F-01 (plumbing) | Slice |
|---|---|---|---|---|
| View own targets/sessions/stats | Allow | Allow | No (no data yet) | S-01+ |
| Upload / photograph a target | Allow | Allow | Route + 401/403 guard only | S-02 (FR-006/007) |
| Accept/reject detection result | Allow (own) | Allow (own) | — | S-03 (FR-009/010/011) |
| View dashboard | Allow | Allow | — | S-01+ (FR-012) |
| GET `/api/me` (auth bootstrap) | Allow | Allow | **Yes** | F-01 |
| Navigate welcome → login → `/` | Allow | Allow | **Yes** | F-01 |
| List all users | Deny (403) | Allow | **Yes (403 plumbing)** | S-04 (FR-003) |
| Remove a user | Deny (403) | Allow | **Yes (403 plumbing)** | S-04 (FR-004) |
| Toggle invite-only registration | Deny (403) | Allow | **Yes (403 plumbing)** | S-04 (FR-005) |

F-01 delivers: `session_auth` + `require_owner` plumbing, `/api/me`, login entry/callback/logout, welcome→main redirect, and **stubbed Owner endpoints returning 403 for non-owners** (real list/remove/invite-only logic deferred to S-04). Owner also uses the app as a shooter — holds all User capabilities plus the admin ones.

## Code References

### Live codebase (current state)
- `src/target_o_meter/settings.py:34-49` — INSTALLED_APPS: `django.contrib.{admin,auth,contenttypes,sessions,messages,staticfiles}`, `django_q`, `src.domains.{core,identity,vision}`. `django_vite` NOT yet installed; `AUTH_USER_MODEL` absent.
- `src/target_o_meter/settings.py:51-60` — MIDDLEWARE: `SecurityMiddleware`, `WhiteNoiseMiddleware`, `SessionMiddleware`, `CommonMiddleware`, `CsrfViewMiddleware`, `AuthenticationMiddleware`, `MessageMiddleware`, `XFrameOptionsMiddleware`. (Session + auth middleware already present — good for the cookie flow.)
- `src/target_o_meter/settings.py:85-90` — DATABASES: SQLite at `Path(os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', BASE_DIR)) / 'db.sqlite3'`.
- `src/target_o_meter/settings.py:114-127` — AUTH_PASSWORD_VALIDATORS: 4 stock validators (irrelevant for OAuth-only login; harmless to leave).
- `src/target_o_meter/urls.py:20-22` — only `path('admin/', admin.site.urls)`. No `/api/`, no app includes.
- `src/domains/identity/models.py` — docstring-only stub (5 lines). Target for the `User` model.
- `src/domains/identity/apps.py:4-6` — `class IdentityConfig(AppConfig): name='src.domains.identity'; label='identity'`. → `AUTH_USER_MODEL = "identity.User"` (app **label**, not dotted path).
- `src/domains/identity/{services,ports,dtos,test_utils}.py` — all docstring-only stubs.
- `src/domains/identity/migrations/__init__.py` — exists, empty. No `0001_initial.py` yet → identity's first migration is free to define the user model.
- `src/bff/__init__.py` — 0 bytes. The entire BFF layer is to be created (no `api.py`, no `routers/`, no `urls.py`).
- `src/frontend/` — directory exists but **empty**. No `package.json`, no `vite.config.*`, no templates dir.

### Pattern references (house style to mirror)
- `src/domains/vision/models.py:13-65` — `ScoringJob` alone in `models.py`; uses `from __future__ import annotations`, UUID PK, `TextChoices` inner class, `class Meta: app_label="vision"; db_table="vision_scoringjob"`. Cross-domain ref as **plain `UUIDField`** (`user_uuid`, `models.py:31`) — explicitly cites AGENTS.md §5 "No Foreign Keys Across Domains".
- `src/domains/vision/services.py:1-323` — **pure** (no django-ninja, no HTTP). Module docstring explicitly cites AGENTS.md §5. This is the contract `identity.services` must obey.
- `src/domains/vision/services.py:238-255` — `get_job(job_id, user_uuid) -> ScoringJobDTO`: takes primitives, returns Pydantic DTO, raises `PermissionError` on owner mismatch (anti-enumeration). **The pattern for `identity.services.get_user_context(sub) -> UserContextDTO`.**
- `src/domains/vision/dtos.py:37-51` — `ScoringJobDTO` (Pydantic `BaseModel`, no ORM). **The pattern for `UserContextDTO` / `MeDTO`.**
- `src/domains/vision/tests/conftest.py:1-129` — session-scoped pytest fixtures; pattern to mirror for identity's seeded-User fixtures.
- `src/domains/vision/tests/test_services_q2.py:90-110` — `test_get_job_enforces_owner_only`: `@pytest.mark.django_db`, `uuid4()` owner vs intruder, `pytest.raises(PermissionError, match=...)`. **The pattern for testing `is_owner`.**
- `src/domains/core/services.py` — docstring-only stub. `log_action` (referenced in AGENTS.md §6.2 example) **does not exist** — out of scope for F-01 (audit logging deferred).

### Env-var convention
- `.env.example` — `GOOGLE_API_KEY`, `OLLAMA_HOST`, `OLLAMA_MODEL`. Convention: `UPPER_SNAKE_CASE`, `os.environ.get(NAME, default)`.
- `src/target_o_meter/settings.py:24,27,29,88` — `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `RAILWAY_VOLUME_MOUNT_PATH` follow the same convention. **New vars**: `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, `AUTH0_DOMAIN`, `OWNER_SUB_ID`, plus optional `SECURE_COOKIES`.

## Architecture Insights

### Integration map (WHERE each piece lands)

| Piece | Location | Constraint |
|---|---|---|
| Custom `User` model | `src/domains/identity/models.py` | `app_label='identity'` (matches `apps.py:5`); set `AUTH_USER_MODEL` before first migration |
| `AUTH_USER_MODEL` | `src/target_o_meter/settings.py` | `AUTH_USER_MODEL = 'identity.User'` |
| Authlib `OAuth` registry + Auth0 register | `src/bff/oauth.py` (new) | HTTP-adjacent → BFF per AGENTS.md §5 |
| login / callback / logout views | `src/bff/routers/auth_routes.py` (new) | Wired via `src/bff/urls.py` → included from `target_o_meter/urls.py` |
| `get_or_create_user_by_sub`, `get_user_context`, `is_owner` | `src/domains/identity/services.py` | Pure Python (mirror `vision/services.py`); returns DTOs |
| Identity DTOs (`UserContextDTO`, `MeDTO`) | `src/domains/identity/dtos.py` | Pydantic `BaseModel` (mirror `vision/dtos.py`) |
| django-ninja API + auth dependency | `src/bff/api.py` (new `NinjaAPI(csrf=True)`) | Registered in `src/bff/urls.py` |
| `/api/me` endpoint | `src/bff/routers/session_routes.py` (new) | Uses `session_auth` |
| Env vars | `.env.example` + `settings.py` | `AUTH0_*`, `OWNER_SUB_ID`, optional `SECURE_COOKIES` |
| Migrations | `src/domains/identity/migrations/0001_initial.py` (new) | Generate **after** `AUTH_USER_MODEL` set; clean-slate DB swap is safe (no user data) |
| Tests (unit/integration) | `src/domains/identity/tests/` (new `test_services.py` + `conftest.py`) | Mirror `vision/tests/test_services_q2.py`; seeders via `identity/test_utils.py` (AGENTS.md §5 — no factory_boy against models) |
| Tests (system) | `tests/system/test_auth_flow.py` (new — first system test) | Needs `tests/system/conftest.py` (also new) |
| SPA entry (if option B) | `templates/index.html` (Django template) + `src/frontend/src/main.jsx` | django-vite template tags; URL catch-all last |

### Import-linter boundary (confirmed)
`.importlinter` declares an `independence` contract across `src.domains.{core,identity,vision}` — **none of the three may import each other** (hard CI gate via `uv run lint-imports`). `src.bff` is NOT in the contract:
- `bff → domain` is **allowed** (BFF is the orchestrator per AGENTS.md §5/§6.2).
- `domain → bff` is forbidden by AGENTS.md §5 (architectural rule; consider adding `src.bff` to the lint contract for a hard gate).
- `identity → vision` / `identity → core` are **forbidden by the lint gate** — cross-domain data passes through DTOs at the BFF seam only.

### Two synthesis decisions resolved
1. **Session-key strategy**: store `user_uuid` (or let `login()` write `_auth_user_id`) — do **not** keep the token blob in the session long-term. Minimizes leakage surface (user's stated goal) and keeps `request.user` populated by `AuthenticationMiddleware`. `sub` is re-derivable from `request.user.sub`.
2. **Owner-source wording**: `roadmap.md:64` says "owner determinable via a configured designated **email**"; `AGENTS.md:21` + `change.md:12` say `OWNER_SUB_ID` (`sub`). The email phrasing is **internally inconsistent** with AGENTS.md §2 "Zero Email Storage." `OWNER_SUB_ID` is the only compatible value. The plan should correct the roadmap line.

## Historical Context (from prior changes)

- `context/archive/2026-07-19-cv-service-boundary/` — F-02 (CV detection service boundary). Established the DDD pattern (`services.py` pure, `ports.py`/`dtos.py` as contract collections, UUID cross-domain refs) that F-01 mirrors in the identity domain. Its `plan.md` is the closest precedent for how to structure a domain-implementation change in this repo.
- `context/foundation/lessons.md`:
  - **"One class per file, matching filename"** — applies to the new `User` model. Exception carved out for `ports.py`/`dtos.py` (contract collections) and "supporting module-level constants and private helpers that serve *only* that class" — covers `UserManager` + `Role` alongside `User` in `models.py`. `vision/models.py` (`ScoringJob` alone) is the house-style precedent for keeping a model in `models.py` rather than `models/user.py`.
  - **"Always set RAILPACK_DJANGO_APP_NAME to the full WSGI module path"** — deployment concern (Railway/Railpack), not directly load-bearing for F-01 but relevant when the auth change ships to Render. The deploy plan (`context/deployment/deploy-plan.md`) already follows this.

## Related Research

- None prior in `context/changes/**/research.md` (this is the first change with research in this repo). The archived CV change (`context/archive/2026-07-19-cv-service-boundary/`) has multiple research files (blob detection, LLM pivot, AI detection) but none touch identity/auth.

## Open Questions

### Q1 (RESOLVED): Auth0 cross-connection `sub` divergence

Auth0 issues a **different `sub` per connection**. The same physical human logging in once via Google and once via magic-link email yields **two different `sub` values** → two rows in `identity_user` → two accounts. ([Auth0 sub vs user_id](https://community.auth0.com/t/auth0-user-id-vs-sub-claim/93311), [account linking](https://auth0.com/docs/manage-users/account-linking))

**Decision (2026-07-24): Option (a) — enable Auth0 account linking.** Auth0 holds the linkage and always returns one canonical `sub` regardless of which connection the user authenticates through. This preserves cross-provider identity continuity and keeps the `OWNER_SUB_ID` match stable even if the owner switches providers.

**Implications the plan must carry (see "Account linking — design implications" below):**
1. Auth0-side configuration step (tenant-level) — documented as a manual setup gate, not code.
2. The canonical `sub` after linking is the **primary** (first-used) identity's `sub` — `OWNER_SUB_ID` must be set to that primary `sub`.
3. **MUST be prompt-based (user-initiated), NOT automatic email-based linking** — see the security caveat below.
4. We store only the canonical `sub`; we do NOT store the list of linked identities (they live in Auth0).

### Account linking — design implications (added 2026-07-24)

Verified the mechanics of Auth0 linking so the plan is grounded in how it actually behaves, not hand-waving:

1. **The `sub` your app receives is the PRIMARY account's `user_id`, and it is stable/invariant.** Once accounts are linked, the ID token issued by Auth0 carries the primary account's `user_id` in `sub` regardless of which linked identity the user authenticated with. So `OWNER_SUB_ID` = the primary's `sub`, and it stays stable even if the owner logs in via a secondary connection. ([User ID Used in Token with Linked Accounts — Auth0 Support](https://support.auth0.com/center/s/article/After-2-accounts-linked-what-will-be-the-user-id-in-the-token-generated-by-Auth0), [Auth0 User ID vs. Sub Claim](https://community.auth0.com/t/auth0-user-id-vs-sub-claim/93311))
2. **The primary `user_id` is fixed at creation/linking time and cannot be changed in a Post-Login Action.** The owner must ensure their primary account is the one they want as canonical — once the primary is set, swapping it requires Auth0 support or unlink/re-link surgery. ([Change primary user during post-login action](https://community.auth0.com/t/change-primary-user-during-post-login-action-results-in-unauthorized/83162), [Unlinking "primary" identity](https://community.auth0.com/t/unlinking-primary-identity/26421))
3. **SECURITY-CRITICAL: linking MUST be prompt-based (user-initiated), NOT automatic-by-email.** Auth0 **removed automatic account-linking samples** from their Rules library because of a real account-takeover vector: an attacker registers a social account using a victim's email; if auto-linking by email is on, the attacker gains access to the victim's existing account. Even "verified email" is unsafe because verification differs per provider (format validation vs. ownership proof). The recommended path is **user-initiated linking**: the user authenticates with BOTH identities before they are merged, proving ownership of each. ([User Account Linking — Auth0 Docs](https://auth0.com/docs/manage-users/user-accounts/user-account-linking), [Security Concerns for automatic account linking](https://community.auth0.com/t/security-concerns-for-automatic-account-linking/122315), [User-Initiated Account Linking: Client-Side Implementation](https://auth0.com/docs/manage-users/user-accounts/user-account-linking/user-initiated-account-linking-client-side-implementation), [Exploiting Auth0 Account Linking](https://nykros.me/exploiting-auth0-misconfigurations-a-case-study-on-account-linking-vulnerabilities))
4. **What our app stores does not change.** We still store only the canonical `sub` (+ nick + derived role). The list of linked identities lives in Auth0's `identities` array; we do not import or persist it. The Zero Email Storage rule is unaffected (we still request `scope="openid profile"`, no email).
5. **Setup is an Auth0-side manual gate, not code.** The plan documents it as a manual step (enable user-initiated linking via an Auth0 Action or the client-side linking flow in Universal Login). No Django code is written for linking itself — the app simply benefits from the stable canonical `sub`.
6. **Impact on F-01 scope:** linking configuration is a **tenant setup task**, not a coding task, so it does not expand F-01's code surface. It lands as a checkbox in the deploy/setup checklist alongside "Allowed Callback URLs" and "Connections: disable Database." The first-login `get_or_create_user_by_sub` logic is unchanged — it just sees a stable `sub`.

**Net for the plan:** the `sub` we store is stable, so the identity model and `OWNER_SUB_ID` design from the research stands as-written. The only new plan items are: (a) a documented manual setup gate for user-initiated linking in Auth0, (b) an explicit "do NOT enable automatic email-based linking" warning in the setup notes, and (c) a note that the owner should pick their primary connection deliberately since the primary `user_id` is fixed.

### Q2 (RESOLVED): React now vs. Django-template main page in F-01

**Decision (2026-07-24): Option (A) — Django templates now, React in S-01.** F-01 ships the welcome/login/main pages as plain Django templates rendering "You are logged in as {nick} ({role})". The full URL contract (`/`, `/bff/login`, `/bff/callback`, `/api/me`) is identical either way, so S-01 swaps the template for React with zero endpoint churn. This keeps F-01 focused on the load-bearing auth + RBAC + session work; the React + Vite + Router + fetch layer lands in S-01 where it belongs.

**Plan impact:** F-01 adds a `templates/` dir (project-level; settings currently has `TEMPLATES[0]['DIRS'] = []`) with a small number of templates (welcome, login button, main shell). `django_vite` stays a reserved dev dependency, NOT installed/wired in F-01. No `package.json`, no `vite.config` in F-01.

### Q3 (RESOLVED): Is `last_login` acceptable?

**Decision (2026-07-24): Keep `last_login`.** It's a nullable timestamp from `AbstractBaseUser`, explicitly acceptable per the research (operational metadata, not identity). Triggers one DB UPDATE per login — negligible at MVP scale. The model definition in §7 stands as-written (no override).

### Q4 (RESOLVED): `OWNER_SUB_ID` empty in production

**Decision (2026-07-24): Add the guard.** A Django system check (or BFF startup assertion) logs a warning when `OWNER_SUB_ID` is empty in non-dev environments. The derived `role` property already fails closed (unset env → no Owner, which is safe), but the guard makes a misconfigured prod deploy loudly visible instead of silently breaking owner access.

**Plan impact:** add a `check()` on the identity app config (or a `ready()` hook) that emits a warning-level system check when `os.environ.get("OWNER_SUB_ID", "")` is empty AND `DEBUG is False`. Pattern: `from django.core.checks import Warning;` register via `@register()` or `appconfigs.ready`.

### Q5 (ACTION ITEM, not a decision): Auth0 sample pins are stale (Django 4.2 + Authlib 1.2)

Our stack is Django 6.0.5 + Authlib 1.6.6 + Python 3.14. Compatibility is fine (the import path `authlib.integrations.django_client` is unchanged in 1.x; [Django 6.0 supports 3.12–3.14](https://docs.djangoproject.com/en/6.0/releases/6.0/)), but do NOT copy the sample's `requirements.txt`. Verify no deprecation warnings from `authlib.integrations.*` on first run. **Action item for the plan**: add `authlib` to `[dependency-groups].default` in `pyproject.toml` (it is NOT currently a declared dependency — the vision domain doesn't use it), and verify import paths against the installed version before committing the BFF OAuth views.

### Q6 (RESOLVED via synthesis): env-var naming across the two research streams

The bypass-middleware agent named the env var `DEV_AUTH_BYPASS_SUB` (a `sub` value, role derived via `OWNER_SUB_ID`); the test-strategy agent referenced it generically as `DEV_AUTH_BYPASS=1`. **Synthesis decision: adopt `DEV_AUTH_BYPASS_SUB`** — the `sub`-carrying form, because it reuses `OWNER_SUB_ID` as the single source of truth for role (see §"Role impersonation" above) and avoids a redundant on/off toggle. The test-strategy references to `DEV_AUTH_BYPASS` are reconciled to this name.

### Q7 (RESOLVED): the UAT approach deliberately bends "passwordless only"

**Decision (2026-07-24): DEFER the UAT Playwright test to a later slice.** F-01 ships only the DEV/UAT **scaffolding** — markers, root conftest (autouse skip), acceptance conftest skeleton, `pytest-playwright` dep, conditional CI job — but **no actual UAT test file yet**. The decision on how the eventual UAT test authenticates (dedicated UAT Database connection vs. Mailtrap-backed magic link) is deferred to the slice that writes it, once the prod flow is more settled.

**What F-01 still delivers** (so the scaffolding isn't vacuous): the marker registration, `addopts = ["-m", "not uat"]`, the autouse skip fixture, and the `uat_auth0_creds` fixture pattern (skip-on-missing). A later slice drops in `tests/acceptance/test_uat_auth_real_auth0.py` and the test just works against the scaffolding. The research above on the DB-connection vs. Mailtrap trade-off is preserved in this doc as the starting point for that later slice's decision.

### Q8 (RESOLVED): OWNER role coverage in the (future) UAT test

**Decision (2026-07-24): when the UAT test lands, parameterize across User + Owner creds** — two test users in the UAT connection, one whose `sub` matches `OWNER_SUB_ID` (Owner), one that doesn't (User), so both role paths are exercised in UAT. This doubles the Auth0 test-user setup but gives full role coverage end-to-end. Since the test itself is deferred (Q7), this is a recorded intent for the later slice, not an F-01 deliverable.

## Development Environment (follow-up research)

Two concerns added after the main research: (1) an optional dev-only auth-bypass middleware so Auth0 doesn't have to be invoked on every local change, and (2) a DEV-vs-UAT test split with exactly one Playwright acceptance test that exercises real Auth0.

### Current test-infra state (verified, greenfield)

- `tests/system/` and `tests/acceptance/` exist but are **empty**.
- **No root `conftest.py`** anywhere outside `.venv`. No `tests/system/conftest.py`.
- **No Playwright config** file. `.github/` directory is absent — no CI yet.
- pyproject `[tool.pytest.ini_options]` has only `DJANGO_SETTINGS_MODULE` + `pythonpath`. **No markers registered.**
- `system-test` dep group has `httpx>=0.28.0`, `playwright>=1.40.0`. **`pytest-playwright` NOT listed.**
- Only existing tests are the per-domain vision tests (`src/domains/vision/tests/`), which use `pytestmark = pytest.mark.django_db` (no `dev`/`uat` markers).

Conclusion: this is greenfield — the research must propose the scaffolding (root conftest, marker registration, acceptance conftest, `pytest-playwright` dep) alongside the bypass middleware.

### Dev-auth-bypass middleware — Approach C (stateless), env-gated

**Decision: a stateless per-request middleware** that sets `request.user = dev_user` when the env var is set, placed immediately after `AuthenticationMiddleware`. It never touches the session.

Why this works (the load-bearing finding, verified against django-ninja source): **`SessionAuth.authenticate()` only checks `request.user.is_authenticated`** — it does NOT re-derive the user from the session cookie, and ninja's operation dispatch never overwrites `request.user`. Source: [`ninja/security/session.py`](https://github.com/vitalik/django-ninja/blob/master/ninja/security/session.py). So a middleware that sets `request.user` to a persisted, `is_authenticated` User makes `SessionAuth` return truthy → route allowed. No framework fight. ([django-ninja auth guide](https://django-ninja.dev/guides/authentication/), [django-ninja #990](https://github.com/vitalik/django-ninja/issues/990))

```python
# src/target_o_meter/dev_auth_bypass.py
import os
from django.contrib.auth import get_user_model
from django.utils.deprecation import MiddlewareMixin

_DEV_USER_CACHE = None  # module-level cache; dev user is immutable for process lifetime

def _get_dev_user():
    global _DEV_USER_CACHE
    if _DEV_USER_CACHE is None:
        sub = os.environ["DEV_AUTH_BYPASS_SUB"]
        User = get_user_model()
        _DEV_USER_CACHE, _ = User.objects.get_or_create(
            sub=sub, defaults={"nick": "dev-" + sub[:8]},
        )
    return _DEV_USER_CACHE

class DevAuthBypassMiddleware(MiddlewareMixin):
    def process_request(self, request):
        if not os.environ.get("DEV_AUTH_BYPASS_SUB"):
            return None
        if getattr(request.user, "is_authenticated", False):
            return None            # a real OAuth login in the same session wins
        request.user = _get_dev_user()
        return None
```

Register in `MIDDLEWARE` right after `AuthenticationMiddleware`. Lives in `src/target_o_meter/` (project/Django-config layer), NOT in `src/domains/identity/` (pure Python) or `src/bff/` — it imports neither django-ninja nor domain code, so it respects AGENTS.md §5. The import-linter `independence` contract (across `src.domains.*`) is unaffected.

**Three rejected alternatives:**
- **Custom `AUTHENTICATION_BACKENDS` entry** — the BFF skips `authenticate()` entirely (calls `login()` directly after the Auth0 callback), so a backend is never invoked. Wrong layer.
- **Middleware that calls `login()`** (stateful, writes session) — fights the real OAuth flow on logout, couples dev identity into persisted session state, cycles the session key. Approach C is strictly simpler.
- **`get_user_model()` at startup / data migration** — startup DB I/O in `ready()` is discouraged; a data migration bakes a dev identity into prod migrations. Lazy get-or-create cached on the module is the right call.

### Production-safety guard (three layers, the check must NOT be `deploy=True`)

This is the most security-sensitive part. A `@register(..., deploy=True)` check only runs under `manage.py check --deploy` — it does NOT fire on `runserver`/`migrate`/WSGI boot, so a misconfigured prod deploy would serve traffic with auth bypassed. The guard MUST be a **plain check that raises `Error`** (not `Warning`), because an `Error` *"will prevent Django commands (such as runserver) from running at all"* ([Django checks](https://docs.djangoproject.com/en/6.0/topics/checks/)).

```python
# src/target_o_meter/checks.py
import os
from django.conf import settings
from django.core.checks import Error, register, Tags

@register(Tags.security)   # NOT deploy=True — must fire on runserver/migrate
def check_dev_auth_bypass_not_in_prod(app_configs, **kwargs):
    errors = []
    sub = os.environ.get("DEV_AUTH_BYPASS_SUB", "").strip()
    if sub and not settings.DEBUG:
        errors.append(Error(
            "DEV_AUTH_BYPASS_SUB is set but DEBUG is False — "
            "authentication would be bypassed in a production-shaped config.",
            hint="Unset DEV_AUTH_BYPASS_SUB, or set DEBUG=True (dev/test only).",
            id="target_o_meter.E001",
        ))
    return errors
```

Registered via `from . import checks  # noqa` at the bottom of `settings.py`. Three-layer defense in depth:

| Failure mode | Layer that catches it |
|---|---|
| Dev forgets to set var in prod | Layer 1 — var absent → no bypass |
| Var leaked into prod env, `DEBUG=False` correct | Layer 2 (DEBUG gate in middleware) + Layer 3 (check `Error` at boot) |
| Var in prod AND `DEBUG` misconfigured `True` | Layer 3 cannot catch — mitigated by Render setting `DEBUG=False` (it does); follow-up could also treat insecure default `SECRET_KEY` / empty `ALLOWED_HOSTS` as prod signals |

**Residual limitation**: the check does not run in the WSGI/ASGI serving stack itself — only before commands. CI must run `manage.py check` as a gate (and Render's build step should too). Layer 2 protects serving-time regardless.

### Role impersonation — single env var `DEV_AUTH_BYPASS_SUB`, reuses `OWNER_SUB_ID`

**Decision: one env var `DEV_AUTH_BYPASS_SUB=<sub>`, role derived via the existing `OWNER_SUB_ID` mechanism.** To impersonate the Owner, set `DEV_AUTH_BYPASS_SUB` to the same value as `OWNER_SUB_ID`; to impersonate a User, set them to different values.

Why this is clean: `OWNER_SUB_ID` stays the **single source of truth** for "who is owner" — there is exactly one code path that decides ownership, used identically in prod and dev. A hypothetical `DEV_AUTH_BYPASS_ROLE=owner|user` would create a split-brain bug (the two role signals could disagree). The mental model is "the bypass lets me pick which `sub` I claim to have; whether that `sub` is owner follows the same rule as prod."

`.env.example` additions (following the existing UPPER_SNAKE + `os.environ.get` convention):

```dotenv
# --- Ownership (who is the project Owner) -----------------------------------
OWNER_SUB_ID=

# --- LOCAL DEV ONLY: authentication bypass ----------------------------------
# Set to a sub to skip the Auth0/OAuth dance locally and be auto-logged-in
# as that user. Leave blank (or unset) in any non-dev environment.
#   - Impersonate a regular USER: set DEV_AUTH_BYPASS_SUB != OWNER_SUB_ID
#   - Impersonate the OWNER:       set DEV_AUTH_BYPASS_SUB == OWNER_SUB_ID
# SAFETY: system check target_o_meter.E001 raises a hard ERROR and refuses to
# start if DEV_AUTH_BYPASS_SUB is set while DEBUG=False.
DEV_AUTH_BYPASS_SUB=
```

Document in `.env.example` (primary discoverability) + a short note in AGENTS.md §7 (Commands). The full *why* (Approach C, three-layer guard) lives in this research doc.

### Interaction with the real OAuth flow

Leave `/bff/login` fully intact. The middleware only overrides `request.user` when the request would otherwise be anonymous (`if request.user.is_authenticated: return None`). So:
- A dev hitting `/bff/login` runs the real Auth0 dance; afterwards `request.user` is the real user, and the bypass defers.
- Logout flushes the session; the next request is anonymous → the bypass re-injects the dev user. This is the desired dev behavior (stay "logged in"); document it so it's not surprising.
- To fully exercise the logged-out UX locally, unset `DEV_AUTH_BYPASS_SUB` and restart.

**Sequencing note**: the middleware references `get_user_model()` and `User.objects.get_or_create(sub=..., defaults={"nick": ...})`, so it can only land AFTER the custom `User` model exists (the main research's §7). The system check + `.env.example` entries can land earlier.

### Test strategy — DEV vs UAT split

**Pattern: pytest markers + an `addopts` default that excludes UAT**, layered on top of the AGENTS-mandated directory layout. (Path-based selection was rejected because this repo's tests live in three places: per-domain `tests/`, `tests/system/`, `tests/acceptance/`.)

`pyproject.toml` additions:

```toml
[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "src.target_o_meter.settings"
pythonpath = ["."]
addopts = [
    "--strict-markers",
    "--strict-config",
    "-m", "not uat",          # bare `uv run pytest` NEVER fires Auth0
]
markers = [
    "dev: fast, Auth0-bypassed tests (default; run on every save).",
    "uat: slow acceptance tests hitting REAL Auth0; skipped unless RUN_UAT=1.",
]
```

UX:
```bash
uv run pytest                       # DEV only (default) — Auth0 bypassed
uv run pytest -m uat                # UAT only — needs RUN_UAT=1 + creds
RUN_UAT=1 uv run pytest             # everything incl. UAT
```

**Belt-and-suspenders**: a root `conftest.py` with an autouse fixture that skips any `@pytest.mark.uat` test unless `RUN_UAT=1` is set — so even `pytest -m uat` won't fire Auth0 without the explicit env opt-in.

```python
# conftest.py (repo root)
import os, pytest

def pytest_configure(config):
    config.addinivalue_line("markers", "dev: fast, Auth0-bypassed tests (default).")
    config.addinivalue_line("markers", "uat: slow acceptance tests hitting REAL Auth0; skipped unless RUN_UAT=1.")

@pytest.fixture(autouse=True)
def _skip_uat_unless_opted_in(request):
    if request.node.get_closest_marker("uat") and not os.environ.get("RUN_UAT"):
        pytest.skip("UAT skipped: set RUN_UAT=1 (requires real Auth0 creds).")
```

`@pytest.mark.dev` is optional in code (DEV is the default); only the UAT test carries an explicit marker. This avoids cluttering the ~10 existing tests.

### The ONE UAT test — DEFERRED (scaffolding ships in F-01, test in a later slice)

**Decision (Q7): F-01 ships the UAT *scaffolding* but NOT the test itself.** The test is deferred because (a) the prod passwordless flow is still settling, and (b) the Auth0-automation approach (dedicated UAT Database connection vs. Mailtrap-backed magic link) is a decision better made in the slice that writes the test. The research below is preserved as the starting point for that later slice.

**The hard part: passwordless auth (magic link + social) is notoriously hard to automate headlessly.** Verified findings:
- Auth0 does NOT provide a "test mode" that disables magic-link verification — for passwordless, the OTP/link IS the authentication factor. ([Auth0 community: bypass passwordless](https://community.auth0.com/t/bypassing-passwordless-verification-for-automated-test-account/39878), [hardcoded OTP](https://community.auth0.com/t/hardcoded-otp-for-automating-test-cases/184058))
- Social automation is extremely fragile — Google/GitHub actively block headless browsers (bot detection, reCAPTCHA). Auth0's own Cypress guide warns that "avoiding social/passwordless flows helps prevent automated rate limiting issues." ([Cypress + Auth0](https://docs.cypress.io/app/guides/authentication-testing/auth0-authentication))
- Magic-link automation requires routing Auth0's email transport to a pollable sink (Mailtrap/Mailosaur) — adds a paid third-party dependency and flakiness.

**The leading candidate for the eventual test (when it lands): a SEPARATE Auth0 Application + `Username-Password-Authentication` Database connection (Password grant) dedicated to UAT.** Playwright logs in with known creds via the real Universal Login UI. The trade-off (tests a flow that doesn't exist in prod) is acceptable because UAT's purpose is wiring validation, not passwordless-feature validation; the prod passwordless UX stays covered by manual login checks before each release. PKCE/state/nonce work cleanly because Playwright drives a real browser. **Q8 decision**: when the test lands, parameterize across two creds sets (a User test user + an Owner test user whose `sub` matches `OWNER_SUB_ID`) so both role paths are exercised.

**Reference shape** (for the later slice — NOT an F-01 deliverable):

```python
# tests/acceptance/test_uat_auth_real_auth0.py  (later slice)
pytestmark = [pytest.mark.uat, pytest.mark.django_db]

@pytest.mark.parametrize("uat_auth0_creds", ["user", "owner"], indirect=True)
def test_real_auth0_login_happy_path(page, live_server, uat_auth0_creds):
    base = live_server.url
    page.goto(base + "/")
    page.get_by_role("button", name="Login").click()
    page.get_by_label("Email").fill(uat_auth0_creds["username"])
    page.get_by_label("Password").fill(uat_auth0_creds["password"])
    page.get_by_role("button", name="Continue").click()
    page.wait_for_url(base + "/app")
    me = page.request.get(base + "/api/me")
    assert me.ok
    body = me.json()
    assert body["sub"] == uat_auth0_creds["expected_sub"]
    assert body["role"] == uat_auth0_creds["expected_role"]
```

- pytest-django's `live_server` fixture spins up `http://localhost:<ephemeral-port>`. Auth0 allows `http://localhost` in Allowed Callback URLs for non-prod apps; the UAT Application's callback list must include `http://localhost:*/bff/callback` (wildcard port).
- **`sub` shape caveat**: a DB-connection user's `sub` is `auth0|...`, while prod users are `google-oauth2|...` / `github|...`. This is fine ONLY IF the identity domain treats `sub` as opaque (per AGENTS.md §2). Confirm via identity-domain tests.

### UAT secrets + CI

Secrets needed: `AUTH0_UAT_DOMAIN`, `AUTH0_UAT_CLIENT_ID`, `AUTH0_UAT_CLIENT_SECRET`, `AUTH0_UAT_USERNAME`, `AUTH0_UAT_PASSWORD`, `AUTH0_UAT_EXPECTED_SUB`. Local: gitignored `.env.uat`. CI: GitHub Actions encrypted secrets.

CI runs UAT **conditionally — skip (not fail) if creds absent** (forks, draft PRs): a `.github/workflows/uat.yml` job gated on both `vars.UAT_ENABLED == 'true'` and same-repo origin, plus the `uat_auth0_creds` fixture skipping per-test if any cred env var is empty. Two layers of skip-protection so a missing secret never turns the build red.

### Greenfield scaffolding to add in this change

F-01 ships the **DEV/UAT scaffolding** but NOT the actual UAT test (deferred, Q7). What lands now:

| Path | Purpose | New/Edit |
|---|---|---|
| `conftest.py` (root) | marker registration + autouse UAT-skip fixture | NEW |
| `tests/acceptance/conftest.py` | `uat_auth0_creds` fixture pattern (skip-on-missing) + `uat_base_url` — ready for the later slice's test to consume | NEW |
| `tests/acceptance/test_uat_auth_real_auth0.py` | the one UAT test — **DEFERRED to a later slice** (Q7); not an F-01 deliverable | (later) |
| `[tool.pytest.ini_options]` | `addopts` (strict + `-m not uat`) + `markers` | EDIT pyproject |
| `[dependency-groups] system-test` | add `pytest-playwright>=0.5.0` | EDIT pyproject |
| `.github/workflows/uat.yml` | conditional UAT CI job — **shell only** in F-01 (job-level `if:` + the per-test skip make it a no-op until a UAT test exists) | NEW |
| `.gitignore` | add `.env.uat` | EDIT |
| `playwright.config.*` | NOT needed — `pytest-playwright` is configured via pytest args + conftest | none |

`tests/acceptance/__init__.py` / `tests/system/__init__.py` — check whether `src/domains/vision/tests/` uses one before adding; if not (pytest discovers via rootdir + `pythonpath=["."]`), omit to match house style.

## Sources

**Auth0 + Authlib**
- [Auth0 Django quickstart](https://auth0.com/docs/quickstart/webapp/django)
- [auth0-samples/auth0-django-web-app (GitHub)](https://github.com/auth0-samples/auth0-django-web-app)
- [Authlib Web Clients (stable)](https://docs.authlib.org/en/stable/oauth2/client/web/index.html)
- [Auth0 Authorization Code Flow with PKCE](https://auth0.com/docs/get-started/authentication-and-authorization-flow/authorization-code-flow-with-pkce)
- [Auth0 Passwordless overview](https://auth0.com/docs/authenticate/passwordless) · [Passwordless with Universal Login](https://auth0.com/docs/authenticate/passwordless/passwordless-with-universal-login)
- [Auth0 Social identity providers](https://auth0.com/docs/authenticate/identity-providers/social-identity-providers)
- [Auth0 How Logout Works](https://auth0.com/docs/authenticate/login/logout) · [Redirect Users After Logout](https://auth0.com/docs/authenticate/login/logout/redirect-users-after-logout)
- [Auth0 OpenID Connect Scopes](https://auth0.com/docs/get-started/apis/scopes/openid-connect-scopes)
- [Auth0 sub vs user_id (community)](https://community.auth0.com/t/auth0-user-id-vs-sub-claim/93311) · [Account linking](https://auth0.com/docs/manage-users/account-linking)
- [SO: does authorize_access_token verify an id_token](https://stackoverflow.com/questions/67637303/does-authorize-access-token-also-verify-an-id-token)

**Django + django-ninja + django-vite**
- [Customizing authentication in Django 6.0](https://docs.djangoproject.com/en/6.0/topics/auth/customizing/) · [django.contrib.auth API](https://docs.djangoproject.com/en/6.0/ref/contrib/auth/) · [Using auth in views (login, user.backend)](https://docs.djangoproject.com/en/6.0/topics/auth/default/)
- [Model constraints (UniqueConstraint, Lower, violation_error_message)](https://docs.djangoproject.com/en/6.0/ref/models/constraints/) · [Django 6.0 settings reference](https://docs.djangoproject.com/en/6.0/ref/settings/) · [Django 6.0 release notes](https://docs.djangoproject.com/en/6.0/releases/6.0/)
- [Django ticket #25313 (mid-project AUTH_USER_MODEL swap)](https://code.djangoproject.com/ticket/25313)
- [django-ninja Authentication](https://django-ninja.dev/guides/authentication/) · [django-ninja CSRF](https://django-ninja.dev/reference/csrf/) · [django-ninja Errors](https://django-ninja.dev/guides/errors/)
- [django-vite README (template tags, manifest, dev_mode)](https://github.com/MrBin99/django-vite)

**SPA cookie-session auth + React routing**
- [Auth0 — Authenticate SPAs with cookies](https://auth0.com/docs/manage-users/cookies/spa-authenticate-with-cookies)
- [Tania Rascia — Full-stack cookies/localStorage (React + same-origin)](https://www.taniarascia.com/full-stack-cookies-localstorage-react-express/)
- [SO — HttpOnly cookie SPA session management](https://stackoverflow.com/questions/42824415/single-page-application-with-httponly-cookie-based-authentication-and-session-ma)
- [MDN — Using the Fetch API (credentials: 'same-origin')](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch)
- [Fireship — Protected routes & auth](https://fireship.dev/react-router-protected-routes-authentication) · [LogRocket — Auth with React Router v7](https://blog.logrocket.com/authentication-react-router-v7/)

**Cookie security**
- [mozilla-django-oidc #497 (SameSite=Lax vs Strict on OIDC callback)](https://github.com/mozilla/mozilla-django-oidc/issues/497)
- [James Bennett — Set Django cookies](https://www.b-list.org/weblog/2023/dec/22/set-django-cookies/)

**Dev-auth-bypass middleware**
- [django-ninja `ninja/security/session.py` (source — SessionAuth trusts request.user)](https://github.com/vitalik/django-ninja/blob/master/ninja/security/session.py)
- [django-ninja `ninja/operation.py` (source — never overwrites request.user)](https://github.com/vitalik/django-ninja/blob/master/ninja/operation.py)
- [django-ninja issue #990 (setting request.user)](https://github.com/vitalik/django-ninja/issues/990)
- [Django 6.0 — System check framework (Error prevents runserver; deploy=True only runs under --deploy)](https://docs.djangoproject.com/en/6.0/topics/checks/)
- [Django 6.0 — How to authenticate using REMOTE_USER (PersistentRemoteUserMiddleware precedent)](https://docs.djangoproject.com/en/6.0/howto/auth-remote-user/)
- [Django 6.0 — Deployment checklist (DEBUG trust-root concern)](https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/)

**Test strategy (DEV vs UAT) + Auth0 automation**
- [pytest — Working with custom markers](https://docs.pytest.org/en/stable/how-to/mark.html)
- [pytest — autouse fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html#autouse-fixtures-fixtures-requested-automatically)
- [Auth0 — B2C Launch: Testing (separate dev/test/prod tenants)](https://auth0.com/docs/get-started/architecture-scenarios/business-to-consumer/launch/testing)
- [Auth0 community — Testing programmatically when passwordless is default (enable password realm grant)](https://community.auth0.com/t/testing-auth0-programatically-when-passwordless-email-is-the-default-directory/190385)
- [Auth0 community — Bypassing passwordless verification for automated tests](https://community.auth0.com/t/bypassing-passwordless-verification-for-automated-test-account/39878)
- [Cypress Docs — Auth0 authentication (avoid social/passwordless for automation)](https://docs.cypress.io/app/guides/authentication-testing/auth0-authentication)
- [Playwright Python — Pytest plugin reference](https://playwright.dev/python/docs/test-runners)
- [Autonoma — Django Playwright Testing: Full Guide (live_server + django_db_blocker)](https://getautonoma.com/blog/django-playwright-testing-guide)
- [pytest-django #1197 — live_server ordering caveat](https://github.com/pytest-dev/pytest-django/issues/1197)
