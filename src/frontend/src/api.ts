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
  // S-03 FR-009 confirmation params (mirror of ScoringJob's columns) so the
  // /results/:jobId screen can pre-fill the accept form with the wizard's picks.
  distance?: number | null;
  weapon_type?: string | null;
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
  distance?: number,
  weapon_type?: string,
): Promise<CreatedScoringJob> {
  const form = new FormData();
  form.append('file', file);
  form.append('target_type', target_type);
  if (caliber_hint) form.append('caliber_hint', caliber_hint);
  if (distance != null) form.append('distance', String(distance));
  if (weapon_type) form.append('weapon_type', weapon_type);
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

// S-03: accept a detection result (FR-010) + the dashboard's aggregation (FR-012).
// The accept route is resource-named (/scoring/results, no {jobId} path param),
// so the job identifier rides in the body. The aggregation route lives under
// /scores/ to distinguish the aggregated-result resource from /scoring/jobs.

export interface AcceptedHole {
  x: number;
  y: number;
  score: number;
  confidence: number;
  caliber?: string | null;
}

export interface AcceptedResult {
  result_id: string;
  source_job: string;
  target_type: string;
  caliber_hint?: string | null;
  distance?: number | null;
  weapon_type?: string | null;
  holes: AcceptedHole[];
  score_average: number;
  created_at?: string | null;
}

export interface HeroStats {
  total_shots: number;
  last_session_average: number | null;
  best_result: number | null;
}

export interface ResultSummary {
  result_id: string;
  source_job: string;
  created_at: string;
  score_average: number;
  hole_count: number;
  target_type: string;
}

export interface DailyAverage {
  date: string;
  average: number;
}

export interface Aggregations {
  hero: HeroStats;
  recent: ResultSummary[];
  daily_averages: DailyAverage[];
}

export async function acceptResult(
  jobId: string,
  payload: {
    target_type: string;
    caliber_hint?: string;
    distance?: number;
    weapon_type?: string;
    holes: AcceptedHole[];
  },
): Promise<AcceptedResult> {
  const res = await fetch('/v1/scoring/results', {
    method: 'POST',
    headers: jsonHeaders(),
    body: JSON.stringify({ job_id: jobId, ...payload }),
  });
  if (!res.ok) throw new Error(`POST /v1/scoring/results failed: ${res.status}`);
  return (await res.json()) as AcceptedResult;
}

export async function getAggregations(): Promise<Aggregations> {
  const res = await fetch('/v1/scores/aggregations', {
    headers: { Accept: 'application/json' },
  });
  if (!res.ok) throw new Error(`GET /v1/scores/aggregations failed: ${res.status}`);
  return (await res.json()) as Aggregations;
}

// user-score-dashboard: the score list + detail + Modify/Delete mutate seam
// onto /v1/scores. The list is paginated (mirrors AdminUserList's shape); the
// detail returns the accepted/corrected snapshot the Modify modal pre-fills.

export interface ScoreList {
  items: ResultSummary[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export async function getScores(
  params: { page?: number; page_size?: number } = {},
): Promise<ScoreList> {
  const qs = new URLSearchParams();
  if (params.page) qs.set('page', String(params.page));
  if (params.page_size) qs.set('page_size', String(params.page_size));
  const suffix = qs.toString() ? `?${qs}` : '';
  const res = await fetch(`/v1/scores${suffix}`, {
    headers: { Accept: 'application/json' },
  });
  if (!res.ok) {
    throw new HttpError(res.status, `GET /v1/scores failed: ${res.status}`);
  }
  return (await res.json()) as ScoreList;
}

export async function getScore(resultId: string): Promise<AcceptedResult> {
  const res = await fetch(`/v1/scores/${resultId}`, {
    headers: { Accept: 'application/json' },
  });
  if (!res.ok) {
    throw new HttpError(res.status, `GET /v1/scores/${resultId} failed: ${res.status}`);
  }
  return (await res.json()) as AcceptedResult;
}

// user-score-dashboard Phase 4: the Modify (PATCH) + Delete mutations.
// Both send the CSRF token via jsonHeaders() (CSRF auto-enforced on SessionAuth
// for non-GET) and throw HttpError(status) on non-ok so the modals can map
// 404 (not-mine / already-gone) to an inline error.

export async function updateScore(
  resultId: string,
  payload: {
    holes: AcceptedHole[];
    target_type?: string;
    caliber_hint?: string;
    distance?: number;
    weapon_type?: string;
  },
): Promise<AcceptedResult> {
  const res = await fetch(`/v1/scores/${resultId}`, {
    method: 'PATCH',
    headers: jsonHeaders(),
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new HttpError(res.status, `PATCH /v1/scores/${resultId} failed: ${res.status}`);
  }
  return (await res.json()) as AcceptedResult;
}

export async function deleteScore(resultId: string): Promise<void> {
  const res = await fetch(`/v1/scores/${resultId}`, {
    method: 'DELETE',
    headers: jsonHeaders(),
  });
  if (!res.ok && res.status !== 204) {
    throw new HttpError(res.status, `DELETE /v1/scores/${resultId} failed: ${res.status}`);
  }
}

// S-04: owner-admin user management. The owner audience is DIFFERENT from
// /v1/me — the list DOES carry `sub` (the owner needs it to match rows against
// Auth0), so these types mirror AdminUserOut / AdminUserListOut (backend),
// not the sub-less UserOut.

export interface BanStatus {
  is_banned: boolean;
  reason: string | null;
  banned_until: string | null;
  lifted_at: string | null;
  has_prior_ban: boolean;
}

export interface AdminUser {
  user_uuid: string;
  sub: string;
  nick: string;
  has_set_nick: boolean;
  is_owner: boolean;
  ban: BanStatus;
}

export interface AdminUserList {
  items: AdminUser[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export class HttpError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = 'HttpError';
  }
}

export async function getAdminUsers(
  params: { q?: string; page?: number; page_size?: number } = {},
): Promise<AdminUserList> {
  const qs = new URLSearchParams();
  if (params.q) qs.set('q', params.q);
  if (params.page) qs.set('page', String(params.page));
  if (params.page_size) qs.set('page_size', String(params.page_size));
  const suffix = qs.toString() ? `?${qs}` : '';
  const res = await fetch(`/v1/users${suffix}`, {
    headers: { Accept: 'application/json' },
  });
  if (!res.ok) {
    throw new HttpError(res.status, `GET /v1/users failed: ${res.status}`);
  }
  return (await res.json()) as AdminUserList;
}

// S-04 Phase 4: owner mutations. All carry the CSRF token via jsonHeaders()
// (CSRF auto-enforced on SessionAuth for non-GET). Each throws HttpError(status)
// on non-ok so the UI can map 409/404 to inline messages.

export type BanDuration = '1h' | '1d' | '7d' | '30d';

export async function banUser(
  userSub: string,
  body: { duration: BanDuration; reason: string },
): Promise<BanStatus> {
  const res = await fetch(`/v1/users/${encodeURIComponent(userSub)}/ban`, {
    method: 'POST',
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new HttpError(res.status, `POST /v1/users/.../ban failed: ${res.status}`);
  return (await res.json()) as BanStatus;
}

export async function unbanUser(userSub: string): Promise<BanStatus> {
  const res = await fetch(`/v1/users/${encodeURIComponent(userSub)}/unban`, {
    method: 'POST',
    headers: jsonHeaders(),
  });
  if (!res.ok) throw new HttpError(res.status, `POST /v1/users/.../unban failed: ${res.status}`);
  return (await res.json()) as BanStatus;
}

export async function deleteUser(userSub: string): Promise<void> {
  const res = await fetch(`/v1/users/${encodeURIComponent(userSub)}`, {
    method: 'DELETE',
    headers: jsonHeaders(),
  });
  if (!res.ok && res.status !== 204) {
    throw new HttpError(res.status, `DELETE /v1/users/... failed: ${res.status}`);
  }
}
