// Phase 3: API client contract tests. Outcome-focused — these pin the
// observable behavior the rest of the SPA depends on, not the fetch wiring:
//   * getMe maps a 401 to the unauthenticated Me shape (the SPA's auth seam).
//   * patchMe/postLogout send the X-CSRFToken header sourced from the
//     csrftoken cookie (Django's SessionAuth enforces CSRF on non-GET).
//   * login performs a full-page navigation to /login (Phase 5: dropped the
//     /v1 prefix from the OIDC chain).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getMe, patchMe, postLogout, login } from './api';

describe('api client', () => {
  // jsdom's window.location is read-only; replace it with a plain stub for
  // the login-redirect test. delete + redefine is the documented jsdom
  // escape hatch. We keep a handle to the original to restore in afterEach.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let originalLocation: any;

  beforeEach(() => {
    originalLocation = window.location;
    // @ts-expect-error — intentional stub; jsdom marks location readonly.
    delete (window as Record<string, unknown>).location;
    // @ts-expect-error — intentional stub
    window.location = { href: '' };
    document.cookie = 'csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
  });

  afterEach(() => {
    // Restore the original jsdom location.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).location = originalLocation;
    vi.restoreAllMocks();
    document.cookie = 'csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
  });

  it('maps a 401 response to the unauthenticated Me shape', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(null, { status: 401 }) as Response
    );
    const me = await getMe();
    expect(me).toEqual({ authenticated: false, user: null });
  });

  it('returns the parsed body on a 200 from getMe', async () => {
    const body = { authenticated: true, user: { nick: 'alice', role: 'user', has_set_nick: true } };
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(body), { status: 200 }) as Response
    );
    const me = await getMe();
    expect(me).toEqual(body);
  });

  it('sends X-CSRFToken from the csrftoken cookie on patchMe', async () => {
    document.cookie = 'csrftoken=test-csrf-token; path=/';
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ authenticated: true, user: { nick: 'alice', role: 'user', has_set_nick: true } }), { status: 200 }) as Response
    );
    await patchMe('alice');
    const [, init] = fetchSpy.mock.calls[0];
    expect(init?.method).toBe('PATCH');
    expect((init?.headers as Record<string, string>)['X-CSRFToken']).toBe('test-csrf-token');
  });

  it('sends X-CSRFToken on postLogout to /logout', async () => {
    document.cookie = 'csrftoken=logout-token; path=/';
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(null, { status: 204 }) as Response
    );
    await postLogout();
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe('/logout');
    expect(init?.method).toBe('POST');
    expect((init?.headers as Record<string, string>)['X-CSRFToken']).toBe('logout-token');
  });

  it('navigates the browser to /login on login()', () => {
    // Per the contract login() never resolves — full-page nav. Assert on the
    // side effect (href assignment), which the stub captures.
    expect(() => login()).toThrow();
    expect(window.location.href).toBe('/login');
  });
});
