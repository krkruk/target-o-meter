// API client for the versioned /v1/ surface (S-01).
//
// Thin typed wrappers over fetch so components don't litter magic strings.
// The SPA's auth seam is getMe(): a 401 (the unauthenticated state) maps to
// the explicit { authenticated: false, user: null } shape so the App can
// branch on me.authenticated without a special null/undefined check.
//
// CSRF: Django's SessionAuth enforces CSRF on non-GET. The SPA reads the
// csrftoken cookie (CSRF_COOKIE_HTTPONLY=False in settings.py) and sends it
// as X-CSRFToken on PATCH /v1/me and POST /logout. (Phase 5: logout lives at
// the URL root alongside login/callback; only the ninja API keeps /v1/.)

export type Role = 'owner' | 'user';

export interface MeUser {
  nick: string;
  role: Role;
  has_set_nick: boolean;
}

export interface Me {
  authenticated: boolean;
  user: MeUser | null;
}

const UNAUTHENTICATED: Me = { authenticated: false, user: null };

function readCsrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

function jsonHeaders(): Record<string, string> {
  return {
    'Content-Type': 'application/json',
    'X-CSRFToken': readCsrfToken(),
  };
}

export async function getMe(): Promise<Me> {
  const res = await fetch('/v1/me', { headers: { Accept: 'application/json' } });
  if (res.status === 401) return UNAUTHENTICATED;
  if (!res.ok) throw new Error(`GET /v1/me failed: ${res.status}`);
  return (await res.json()) as Me;
}

export async function patchMe(nick: string): Promise<Me> {
  const res = await fetch('/v1/me', {
    method: 'PATCH',
    headers: jsonHeaders(),
    body: JSON.stringify({ nick }),
  });
  if (!res.ok) throw new Error(`PATCH /v1/me failed: ${res.status}`);
  return (await res.json()) as Me;
}

export async function postLogout(): Promise<void> {
  const res = await fetch('/logout', {
    method: 'POST',
    headers: jsonHeaders(),
  });
  if (!res.ok && res.status !== 302) {
    // 302 is the expected logout redirect to Auth0 /v2/logout; treat it as
    // success. Anything else is a real failure.
    throw new Error(`POST /logout failed: ${res.status}`);
  }
}

export function login(): never {
  // Full-page navigation to the OIDC redirect chain. Never resolves — the
  // browser leaves the SPA. Throwing satisfies the `never` return type and
  // stops execution in tests that mock window.location.
  // Phase 5: dropped the /v1 prefix from the OIDC chain (login/callback/logout
  // live at the URL root; /v1/ stays the version root for the ninja API only).
  window.location.href = '/login';
  throw new Error('navigating to /login');
}

// S-02: the scoring seam onto POST /v1/scoring/jobs + GET /v1/scoring/jobs/{id}.
// The SPA's capture/upload wizard calls createScoringJob (multipart), then
// /waiting/:jobId polls getScoringJob until a terminal status.

export type ScoringJobStatus = 'queued' | 'running' | 'succeeded' | 'failed';

export interface DetectedHole {
  x: number;
  y: number;
  score: number;
  confidence: number;
  caliber?: string | null;
}

export interface ScoringResult {
  holes: DetectedHole[];
  target_type: string;
  notes?: string | null;
  detector_name: string;
}

export interface ScoringJob {
  job_id: string;
  status: ScoringJobStatus;
  target_type: string;
  caliber_hint?: string | null;
  result?: ScoringResult | null;
  error?: string | null;
  created_at?: string | null;
  completed_at?: string | null;
  marked_image_url?: string | null;
}

export interface CreatedScoringJob {
  job_id: string;
  status: string;
}

function multipartHeaders(): Record<string, string> {
  // Multipart differs from the JSON helpers: the browser MUST set the
  // Content-Type (it includes the randomly-generated boundary). Pinning
  // Content-Type here would strip the boundary and the server couldn't parse
  // the body — so only X-CSRFToken is sent.
  return { 'X-CSRFToken': readCsrfToken() };
}

export async function createScoringJob(
  file: File,
  target_type: string,
  caliber_hint?: string,
  distance_m?: number,
): Promise<CreatedScoringJob> {
  const form = new FormData();
  form.append('file', file);
  form.append('target_type', target_type);
  if (caliber_hint) form.append('caliber_hint', caliber_hint);
  if (distance_m != null) form.append('distance_m', String(distance_m));
  const res = await fetch('/v1/scoring/jobs', {
    method: 'POST',
    headers: multipartHeaders(),
    body: form,
  });
  if (!res.ok) throw new Error(`POST /v1/scoring/jobs failed: ${res.status}`);
  return (await res.json()) as CreatedScoringJob;
}

export async function getScoringJob(jobId: string): Promise<ScoringJob> {
  const res = await fetch(`/v1/scoring/jobs/${jobId}`, {
    headers: { Accept: 'application/json' },
  });
  if (!res.ok) throw new Error(`GET /v1/scoring/jobs/${jobId} failed: ${res.status}`);
  return (await res.json()) as ScoringJob;
}
