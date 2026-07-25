// API client for the versioned /v1/ surface (S-01).
//
// Thin typed wrappers over fetch so components don't litter magic strings.
// The SPA's auth seam is getMe(): a 401 (the unauthenticated state) maps to
// the explicit { authenticated: false, user: null } shape so the App can
// branch on me.authenticated without a special null/undefined check.
//
// CSRF: Django's SessionAuth enforces CSRF on non-GET. The SPA reads the
// csrftoken cookie (CSRF_COOKIE_HTTPONLY=False in settings.py) and sends it
// as X-CSRFToken on PATCH /v1/me and POST /v1/logout.

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
  const res = await fetch('/v1/logout', {
    method: 'POST',
    headers: jsonHeaders(),
  });
  if (!res.ok && res.status !== 302) {
    // 302 is the expected logout redirect to Auth0 /v2/logout; treat it as
    // success. Anything else is a real failure.
    throw new Error(`POST /v1/logout failed: ${res.status}`);
  }
}

export function login(): never {
  // Full-page navigation to the OIDC redirect chain. Never resolves — the
  // browser leaves the SPA. Throwing satisfies the `never` return type and
  // stops execution in tests that mock window.location.
  window.location.href = '/v1/login';
  throw new Error('navigating to /v1/login');
}
