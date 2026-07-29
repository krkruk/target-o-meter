// Railway Infrastructure-as-Code — single-service Free-tier topology.
//
// Declares the full prod topology so future resource drift is caught by
// `railway config apply`. Secrets use `preserve()` placeholders: the human
// sets the values in the Railway dashboard (Phase 7); the IaC never sees them,
// and `config apply` never clobbers what's already there.
//
// Shape (single service, not 2-service web+worker): django-q2 uses the SQLite
// DB as its ORM broker, and a Railway Volume attaches to exactly ONE service —
// so a separate worker can't reach the broker DB (SQLite has no network
// listener). gunicorn + qcluster share one container + one Volume. See
// research.md §"headline inversion" + plan §What We're NOT Doing.
//
// WORKFLOW:
//   Phase 7 (human, dashboard): create project + Volume + Bucket, set secrets.
//   Phase 8 (human, CLI): `railway link` → `railway config apply` once to
//     reconcile this file against the existing project. preserve() guards the
//     manually-set secrets from being cleared.
//   Thereafter (CI): `railway up` only — never `config apply` (see
//   .github/actions/deploy-railway/action.yml).
//
// RAILPACK PIVOT: the root Dockerfile is RETAINED for local docker-compose dev
// only; prod builds via Railpack. `BUILDER: "railpack"` overrides Railway's
// Dockerfile auto-detection (infrastructure.md:93 Risk Register mandate is
// overridden by user direction; the opencv apt-deps mitigation that originally
// justified the Dockerfile path now lives in railpack.json deploy.aptPackages).

import {
  bucket,
  defineRailway,
  github,
  group,
  preserve,
  project,
  service,
  volume,
} from "railway/iac";

export default defineRailway((_ctx) => {
  // `ctx.environment === "production"` is intentionally not branched on today:
  // the same single-service topology applies to every environment. When a
  // staging/prod split is needed (e.g. staging without the bucket), branch on
  // `_ctx.environment` here — the DSL's `defineRailway((ctx) => …)` callback
  // exposes `ctx.environment`, `ctx.isEnvironment(name)`, etc.

  // SQLite volume. region "europe-west4" is the friendly name for the
  // europe-west4-drams3a (Amsterdam) host. 500 MB is Railway's actual Free-tier
  // Volume granularity (the plan draft said "512 MB cap"; Railway provisions at
  // 500 — the IaC must match the provisioned value or `config apply` flags
  // drift). Live Resize is Hobby+ only, so on Free you cannot grow this without
  // upgrading. SQLite MVP footprint is <100 MB → ~5x headroom.
  const data = volume("data", {
    region: "europe-west4",
    sizeMB: 500,
  });

  // Tigris-backed object storage for uploads + per-job deliverables. Region
  // "auto" is Tigris's global addressing — Railway Storage Buckets provision
  // buckets as region "auto" regardless of compute region, and bucket region is
  // IMMUTABLE after creation (the europe-west4 compute co-locates at the network
  // layer; the bucket itself is globally addressed). The IaC must match the
  // provisioned value or `config apply` will flag drift.
  const uploads = bucket("uploads", { region: "auto" });

  // Shared prod env. The q2 task body reads S3 creds, GOOGLE_API_KEY,
  // VISION_DETECTOR, AUTH0 — gunicorn + qcluster share this (single container).
  const env = {
    // --- Literals (non-secret; config apply sets these from this file) ---
    PYTHONPATH: "/app", // project not editable-installed (pyproject name is the
    //                "module1" placeholder); gunicorn + qcluster subprocesses
    //                need /app on sys.path to import src.* (mirrors
    //                docker-compose.prod.yml:23-24, 58-60).
    DEBUG: "False", // E001/E002/W002 production guards depend on this (checks.py).
    SECURE_COOKIES: "True", // Railway terminates TLS → SECURE cookie flags +
    //                       SECURE_PROXY_SSL_HEADER (settings.py:223-251).
    USE_S3: "True", // flip STORAGES['default'] to Tigris (settings.py:346-358).
    AWS_S3_ENDPOINT_URL: "https://t3.storageapi.dev", // Tigris S3 endpoint.
    //   REQUIRED — settings.py:366 reads this with os.environ.get() and
    //   defaults to None; with None, boto3 targets s3.amazonaws.com (a
    //   different host) and the first upload fails with an S3 connection
    //   error. The "unset for Tigris" assumption in .env.example only holds if
    //   Railway injects the endpoint via a bucket-variable reference — we use
    //   raw preserve() creds, so no Railway magic applies. Value taken from the
    //   bucket's Connect panel. (research Open Q #5 — resolved up-front.)
    AWS_S3_ADDRESSING_STYLE: "virtual-host", // Tigris urlStyle from the bucket's
    //   Connect panel. settings.py:367 reads this; "virtual-host" addresses the
    //   bucket as <bucket>.t3.storageapi.dev (the Tigris shape) rather than the
    //   path-style "auto" default.
    VISION_DETECTOR: "google", // prod detector (requires GOOGLE_API_KEY).
    Q2_WORKERS: "1", // narrow django-q2 to 1 worker (Free-tier RAM budget,
    //                ≤10 users). settings.py reads Q2_WORKERS with a local
    //                default of 3, so `make dev` is unaffected. See plan §1.2.
    BUILDER: "railpack", // override Railway's Dockerfile auto-detection (the
    //                     root Dockerfile is retained for local dev only).
    RAILPACK_DJANGO_APP_NAME: "target_o_meter.wsgi", // lessons.md:5-10 — load-
    //   bearing under Railpack. Without it, the Django detector may build
    //   `gunicorn module1:application` (the pyproject placeholder name) and
    //   crash-loop on "Failed to find attribute 'application'". Defense-in-
    //   depth: the start command below names the full WSGI module path anyway.

    // --- Secrets (preserve() — human sets values in dashboard, Phase 7) ---
    GOOGLE_API_KEY: preserve(), // Google AI Studio (Gemini vision).
    OWNER_SUB_ID: preserve(), // copy from first prod login WARN log (W001 if empty).
    AUTH0_CLIENT_ID: preserve(), // Auth0 OIDC client.
    AUTH0_CLIENT_SECRET: preserve(),
    AUTH0_DOMAIN: preserve(), // e.g. your-tenant.eu.auth0.com
    SECRET_KEY: preserve(), // = same value as AUTH0_SECRET (E002 blocks the
    AUTH0_SECRET: preserve(), //   insecure fallback under DEBUG=False).

    // Tigris bucket creds. Safe path: human provisions the bucket in the
    // dashboard (Phase 7) → Railway injects these into the service env →
    // preserve() keeps `config apply` from clearing them. The DSL docs do NOT
    // document a `uploads.env.AWS_*` accessor (only db.env.* / service.env.*),
    // so preserve() is the verified-safe option. If a bucket cred accessor is
    // later confirmed, prefer it (research Open Q #3, verified in Phase 8).
    AWS_ACCESS_KEY_ID: preserve(),
    AWS_SECRET_ACCESS_KEY: preserve(),
    AWS_STORAGE_BUCKET_NAME: preserve(),

    // Explicitly NOT set in prod (E001 boot-blocks DEV_AUTH_BYPASS_SUB under
    // DEBUG=False; the rest are dev-only):
    //   DEV_AUTH_BYPASS_SUB, DEV_ADMIN_*, MOCK_DETECTOR_*, OLLAMA_*,
    //   DJANGO_VITE_DEV_MODE (omit → DEBUG=False → manifest mode),
    //   TOM_ENV_FILE (no .env file in prod).
    // Explicitly NOT set (Railway auto-injects from volumeMounts):
    //   RAILWAY_VOLUME_MOUNT_PATH  (= "/data" → DB path /data/db.sqlite3,
    //   settings.py:263-268).
  };

  const web = service("web", {
    source: github("krkruk/target-o-meter", { branch: "master" }),

    // Build path: Railpack (BUILDER=railpack above). Migrate runs in START,
    // NOT preDeployCommand: Railway volumes are NOT mounted during pre-deploy,
    // so migrate must run post-mount against /data/db.sqlite3 (research.md:184).
    // qcluster is backgrounded (`&`); gunicorn is foreground (`exec`) for a
    // clean SIGTERM. No `uv run` prefix — the Railpack venv is activated at
    // container start (verify on first deploy, Phase 8).
    //
    // FREE-TIER RAM BUDGET: Free caps RAM at 0.5 GB/service. `--workers 1`
    // keeps gunicorn's resident footprint minimal (~40-60 MB/worker);
    // `--workers 3` (the docker-compose.prod.yml default) is a Hobby-tier
    // shape. Q2_WORKERS=1 above does the same for q2. See research.md §Budget.
    start:
      'sh -c "python src/manage.py migrate --noinput && ' +
      'python src/manage.py qcluster & ' +
      'exec gunicorn src.target_o_meter.wsgi:application ' +
      '--bind 0.0.0.0:8000 --workers 1"',

    // The Phase 1 view (src/target_o_meter/views.py) returns 200 "ok" with no
    // DB access. healthcheck is a STRING path (railway/iac DSL), not an object.
    healthcheck: "/health",
    healthcheckTimeout: 30,

    // Single replica in europe-west4. Volumes forbid >1 replica; Free plan
    // also caps at 1. The region key co-locates compute with the Volume.
    replicas: { "europe-west4": 1 },

    // RAILWAY_VOLUME_MOUNT_PATH=/data is auto-injected from this mount.
    // settings.py:263 resolves DB to <RAILWAY_VOLUME_MOUNT_PATH>/db.sqlite3.
    volumeMounts: { "/data": data },

    domains: ["target-o-meter.up.railway.app"], // the project's custom Railway
    //   production domain (attached to the web service, syncStatus ACTIVE,
    //   confirmed 2026-07-29). supersedes the auto-generated
    //   web-production-5c61a.up.railway.app.

    env: {
      ...env,
      APP_BASE_URL: "https://target-o-meter.up.railway.app", // the live
      //   production domain. ALLOWED_HOSTS derives from this host
      //   (settings.py:92-99). No trailing slash — both consumers parse it
      //   with urlparse().hostname, so a slash is harmless, but the canonical
      //   form matches Railway's domain convention.
    },
  });

  const app = group("App", [web, data, uploads]);

  // Same resources in prod and non-prod for now (single topology). Splitting
  // later (e.g. staging without the bucket) would branch on `prod` here.
  return project("target-o-meter", { resources: [app] });
});
