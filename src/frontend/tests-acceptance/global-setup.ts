// Playwright globalSetup: boot the full dev stack once for the whole run.
//
// Boots three subprocesses sharing a clean SQLite DB under
// results/playwright-<run-id>/:
//   1. Django runserver (DEBUG=True, VISION_DETECTOR=mock, DEV_AUTH_BYPASS_SUB
//      set so the SPA boots authed without Auth0) on an ephemeral port.
//   2. Vite dev server (:5173) — django-vite proxies the SPA bundle to it.
//   3. django-q2 worker (qcluster) — runs process_image so POST /v1/scoring/
//      jobs actually transitions queued -> running -> succeeded with the 5
//      MockDetector holes (without a worker the job stays queued forever).
//
// Readiness: hit the SPA root until it returns 200 (Django up) AND poll the
// Vite dev server's main.tsx until it returns JS (Vite up). The worker is
// best-effort — it boots alongside; the scoring tests poll until succeeded.
//
// The stack handle (ports + PIDs) is stashed on globalThis so the teardown can
// kill the whole process group, and so tests can read the Django base URL.
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdirSync, existsSync, rmSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
// Repo root: src/frontend/tests-acceptance/ -> repo root is three levels up.
const REPO_ROOT = resolve(__dirname, '..', '..', '..');
const MANAGE_PY = resolve(REPO_ROOT, 'src', 'manage.py');
const RUN_DIR = resolve(REPO_ROOT, 'results', `playwright-${process.pid}`);

export interface StackHandle {
  djangoBase: string;
  djangoPort: number;
  vitePort: number;
  runDir: string;
  processes: ChildProcess[];
}

async function waitForHttp(url: string, { wantStatus = 200, timeoutMs = 30_000, acceptContains }: { wantStatus?: number; timeoutMs?: number; acceptContains?: string } = {}): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastErr = '';
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.status === wantStatus || (wantStatus === 0 && res.status < 500)) {
        if (!acceptContains) return;
        const ct = res.headers.get('content-type') || '';
        if (ct.includes(acceptContains)) return;
      }
    } catch (e) {
      lastErr = e instanceof Error ? e.message : String(e);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`waitForHttp timed out waiting for ${url} (wantStatus=${wantStatus}${acceptContents(acceptContains)}); last error: ${lastErr}`);
  function acceptContents(s?: string) { return s ? `, want content-type containing ${s}` : ''; }
}

function spawnManaged(args: string[], env: NodeJS.ProcessEnv): ChildProcess {
  const proc = spawn(args[0], args.slice(1), {
    cwd: resolve(REPO_ROOT, 'src'),
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: true,
  });
  // Inherit logs to stderr so failures surface in the Playwright output.
  proc.stdout?.on('data', (d) => process.stderr.write(`[stack:${args.join(' ')}] ${d}`));
  proc.stderr?.on('data', (d) => process.stderr.write(`[stack:${args.join(' ')}] ${d}`));
  return proc;
}

export default async function globalSetup(): Promise<void> {
  if (existsSync(RUN_DIR)) rmSync(RUN_DIR, { recursive: true, force: true });
  mkdirSync(RUN_DIR, { recursive: true });

  // Fixed ports so the playwright.config.ts baseURL (8187) matches without a
  // config↔setup handoff. Acceptance tests run single-worker (no parallelism),
  // and the globalSetup owns the whole lifecycle, so a fixed port is safe —
  // nothing else competes for it during the run.
  const djangoPort = Number(process.env.SPA_DJANGO_PORT) || 8187;
  const vitePort = Number(process.env.SPA_VITE_PORT) || 5173;

  // Shared env: clean DB + dev bypass + mock detector.
  const baseEnv: NodeJS.ProcessEnv = {
    ...process.env,
    RAILWAY_VOLUME_MOUNT_PATH: RUN_DIR,
    STATIC_ROOT: resolve(RUN_DIR, 'staticfiles'),
    DJANGO_SETTINGS_MODULE: 'src.target_o_meter.settings',
    DEBUG: 'True',
    DEV_AUTH_BYPASS_SUB: 'auth0|playwright-acceptance',
    OWNER_SUB_ID: 'auth0|playwright-acceptance',
    VISION_DETECTOR: 'mock',
    // S-03: pin the MockDetector's random pattern so hole-count assertions in
    // the specs are deterministic (seeded + count pinned to 5).
    MOCK_DETECTOR_SEED: '42',
    MOCK_DETECTOR_HOLE_COUNT: '5',
    // Auth0 vars can stay empty — the dev bypass short-circuits OAuth.
    AUTH0_SECRET: 'a'.repeat(64),
    SECRET_KEY: 'a'.repeat(64),
    APP_BASE_URL: `http://127.0.0.1:${djangoPort}`,
    USE_S3: 'False',
  };

  // 1. migrate (synchronous — the runserver would auto-migrate but we want
  //    the worker + Django to agree the schema is ready before serving).
  await new Promise<void>((resolveMig, rejectMig) => {
    const migrate = spawn('uv', ['run', 'python', MANAGE_PY, 'migrate', '--noinput'], {
      cwd: resolve(REPO_ROOT, 'src'),
      env: baseEnv,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stderr = '';
    migrate.stderr?.on('data', (d) => { stderr += d; process.stderr.write(`[migrate] ${d}`); });
    migrate.on('error', rejectMig);
    migrate.on('exit', (code) => {
      if (code === 0) resolveMig();
      else rejectMig(new Error(`migrate exit ${code}\n${stderr}`));
    });
  });

  // Seed the dev-bypass user with has_set_nick=True so the SPA's NickPrompt
  // doesn't overlay the dashboard (the bypass creates the row on first
  // request, but with has_set_nick=False). Goes through the app's service
  // surface (AGENTS.md §5) via manage.py shell.
  //
  // S-04: also seed a plain (non-owner) target user so the owner-management
  // acceptance spec has a row to ban/unban/delete. Re-running is idempotent
  // (get_or_create); if the spec deleted it last run, it's recreated.
  await new Promise<void>((resolveSeed, rejectSeed) => {
    const seed = spawn(
      'uv', ['run', 'python', MANAGE_PY, 'shell', '-c',
        `from src.domains.identity.models import User
u, _ = User.objects.get_or_create(sub='auth0|playwright-acceptance', defaults={'nick': 'acceptance-runner'})
if not u.has_set_nick:
    u.has_set_nick = True
    u.save(update_fields=['has_set_nick'])
User.objects.get_or_create(sub='auth0|acceptance-target', defaults={'nick': 'target-acct'})
print('seeded acceptance users')`],
      { cwd: resolve(REPO_ROOT, 'src'), env: baseEnv, stdio: ['ignore', 'pipe', 'pipe'] },
    );
    let stderr = '';
    seed.stderr?.on('data', (d) => { stderr += d; process.stderr.write(`[seed] ${d}`); });
    seed.on('error', rejectSeed);
    seed.on('exit', (code) => {
      if (code === 0) resolveSeed();
      else rejectSeed(new Error(`seed exit ${code}\n${stderr}`));
    });
  });

  // 2. Vite dev server (django-vite proxies the bundle to it).
  const vite = spawn('npm', ['run', 'dev', '--', '--port', String(vitePort), '--strictPort'], {
    cwd: resolve(REPO_ROOT, 'src', 'frontend'),
    env: { ...baseEnv, PATH: process.env.PATH || '' },
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: true,
  });
  vite.stdout?.on('data', (d) => process.stderr.write(`[vite] ${d}`));
  vite.stderr?.on('data', (d) => process.stderr.write(`[vite] ${d}`));

  // 3. Django runserver.
  const django = spawnManaged(
    ['uv', 'run', 'python', MANAGE_PY, 'runserver', `127.0.0.1:${djangoPort}`, '--noreload'],
    baseEnv,
  );

  // 4. qcluster worker (drives process_image).
  const worker = spawnManaged(
    ['uv', 'run', 'python', MANAGE_PY, 'qcluster'],
    baseEnv,
  );

  // Wait for both servers. Vite serves under its base path (/static/); hit
  // the module it proxies. Use localhost (Vite binds there, not 127.0.0.1 by
  // default) for the Vite poll, 127.0.0.1 for Django.
  await waitForHttp(`http://localhost:${vitePort}/static/src/main.tsx`, { acceptContains: 'javascript', timeoutMs: 40_000 }).catch((e) => {
    process.stderr.write(`[globalSetup] Vite readiness probe failed: ${e}\n`);
    throw e;
  });
  await waitForHttp(`http://127.0.0.1:${djangoPort}/`, { wantStatus: 200, timeoutMs: 40_000 });

  const handle: StackHandle = {
    djangoBase: `http://127.0.0.1:${djangoPort}`,
    djangoPort,
    vitePort,
    runDir: RUN_DIR,
    processes: [django, worker, vite],
  };
  (globalThis as unknown as { __stack: StackHandle }).__stack = handle;
  writeFileSync(resolve(RUN_DIR, 'stack-handle.json'), JSON.stringify({ djangoBase: handle.djangoBase }, null, 2));
}
