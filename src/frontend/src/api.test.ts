// Phase 3: API client contract tests. Outcome-focused — these pin the
// observable behavior the rest of the SPA depends on, not the fetch wiring:
//   * getMe maps a 401 to the unauthenticated Me shape (the SPA's auth seam).
//   * patchMe/postLogout send the X-CSRFToken header sourced from the
//     csrftoken cookie (Django's SessionAuth enforces CSRF on non-GET).
//   * login performs a full-page navigation to /login (Phase 5: dropped the
//     /v1 prefix from the OIDC chain).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getMe, patchMe, postLogout, login, createScoringJob, getScoringJob } from './api';

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

  // S-02 Phase 6.4: the multipart upload + poll helpers onto the scoring seam.
  describe('createScoringJob (multipart upload)', () => {
    it('POSTs a FormData body to /v1/scoring/jobs with X-CSRFToken and NO Content-Type', async () => {
      // The browser must set the multipart boundary — the client MUST NOT pin
      // Content-Type (doing so drops the boundary and the server can't parse
      // the body). This is the load-bearing difference from the JSON helpers.
      document.cookie = 'csrftoken=upload-token; path=/';
      const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
        new Response(JSON.stringify({ job_id: 'abc', status: 'queued' }), { status: 201 }) as Response
      );
      const file = new File([new Uint8Array([1, 2, 3])], 'target.jpg', { type: 'image/jpeg' });

      const result = await createScoringJob(file, 'air_pistol', '9x19mm', 25);

      const [url, init] = fetchSpy.mock.calls[0];
      expect(url).toBe('/v1/scoring/jobs');
      expect(init?.method).toBe('POST');
      expect((init?.headers as Record<string, string>)['X-CSRFToken']).toBe('upload-token');
      // Content-Type must be absent so the browser sets the multipart boundary.
      const headers = init?.headers as Record<string, string>;
      expect(headers['Content-Type']).toBeUndefined();
      expect(headers['content-type']).toBeUndefined();
      // The body is a FormData carrying the file + the form fields.
      expect(init?.body).toBeInstanceOf(FormData);
      const body = init?.body as FormData;
      expect(body.get('file')).toBe(file);
      expect(body.get('target_type')).toBe('air_pistol');
      expect(body.get('caliber_hint')).toBe('9x19mm');
      expect(body.get('distance_m')).toBe('25');
      expect(result).toEqual({ job_id: 'abc', status: 'queued' });
    });

    it('omits optional form fields when not provided', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(
        new Response(JSON.stringify({ job_id: 'xyz', status: 'queued' }), { status: 201 }) as Response
      );
      const file = new File([new Uint8Array([1])], 't.jpg', { type: 'image/jpeg' });
      await createScoringJob(file, 'precision_pistol');
      const body = vi.mocked(globalThis.fetch).mock.calls[0][1]?.body as FormData;
      expect(body.get('target_type')).toBe('precision_pistol');
      expect(body.has('caliber_hint')).toBe(false);
      expect(body.has('distance_m')).toBe(false);
    });

    it('throws when the POST fails (non-2xx)', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(
        new Response(null, { status: 422 }) as Response
      );
      const file = new File([new Uint8Array([1])], 't.jpg', { type: 'image/jpeg' });
      await expect(createScoringJob(file, 'air_pistol')).rejects.toThrow(/422/);
    });
  });

  describe('getScoringJob (poll)', () => {
    it('GETs /v1/scoring/jobs/{jobId} with Accept: application/json', async () => {
      const body = {
        job_id: 'abc', status: 'succeeded', target_type: 'air_pistol',
        result: { holes: [{ x: 512, y: 512, score: 10, confidence: 1 }], target_type: 'air_pistol', detector_name: 'mock' },
      };
      const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
        new Response(JSON.stringify(body), { status: 200 }) as Response
      );
      const result = await getScoringJob('abc');
      const [url, init] = fetchSpy.mock.calls[0];
      expect(url).toBe('/v1/scoring/jobs/abc');
      expect((init?.headers as Record<string, string>)['Accept']).toBe('application/json');
      expect(result.status).toBe('succeeded');
      expect(result.result?.holes).toHaveLength(1);
    });

    it('throws when the GET fails', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(
        new Response(null, { status: 404 }) as Response
      );
      await expect(getScoringJob('missing')).rejects.toThrow(/404/);
    });
  });
});
