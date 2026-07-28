// S-02 Phase 8 + S-03: Results route contract. Fetches the ScoringJob, renders
// the marked image + per-hole correction dropdowns, and (S-03) the confirm-
// parameters form + Accept/Reject buttons. Accept → POST /v1/scoring/results
// with the corrected holes + params → /dashboard. Reject → /dashboard (no POST).
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import * as api from '../api';
import type { ScoringJob } from '../api';
import { Results } from './Results';

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/results/:jobId" element={<Results />} />
      </Routes>
    </MemoryRouter>
  );
}

// Renders with a Probe that exposes the current pathname (for navigation asserts).
function renderAtWithProbe(path: string) {
  let currentPath = '';
  function Probe() { currentPath = useLocation().pathname; return null; }
  const utils = render(
    <MemoryRouter initialEntries={[path]}>
      <Probe />
      <Routes>
        <Route path="/results/:jobId" element={<Results />} />
        <Route path="/dashboard" element={<div>dashboard-sentinel</div>} />
      </Routes>
    </MemoryRouter>
  );
  return { ...utils, getPath: () => currentPath };
}

function makeJob(overrides: Partial<ScoringJob> = {}): ScoringJob {
  return {
    job_id: 'abc',
    status: 'succeeded',
    target_type: 'air_pistol',
    caliber_hint: '9x19mm',
    distance: 25,
    weapon_type: 'sport_pistol',
    marked_image_url: '/media/jobs/abc/marked.png',
    result: {
      holes: [
        { x: 512, y: 512, score: 10, confidence: 1 },
        { x: 712, y: 512, score: 7, confidence: 0.9 },
      ],
      target_type: 'air_pistol',
      detector_name: 'mock',
    },
    ...overrides,
  };
}

describe('Results', () => {
  beforeEach(() => {
    // jsdom doesn't implement matchMedia; Results doesn't branch on it but the
    // stub is harmless and keeps the test environment consistent.
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the marked image + per-hole correction dropdowns on a populated result', async () => {
    const job = makeJob();
    vi.spyOn(api, 'getScoringJob').mockResolvedValue(job);
    renderAt('/results/abc');
    await waitFor(() => {
      expect(screen.getByRole('img', { name: /marked target/i })).toHaveAttribute('src', job.marked_image_url!);
    });
    // One correction <select> per hole + 4 confirm-params selects (caliber,
    // distance, weapon_type, target_type) + the Accept/Reject buttons (S-03).
    const selects = screen.getAllByRole('combobox');
    expect(selects).toHaveLength(job.result!.holes.length + 4);
    expect(screen.getByRole('button', { name: /accept result/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reject result/i })).toBeInTheDocument();
  });

  it('renders an "unable to load results" fallback when result is null', async () => {
    vi.spyOn(api, 'getScoringJob').mockResolvedValue(
      makeJob({ result: null, marked_image_url: '/media/x.png' })
    );
    renderAt('/results/abc');
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
    expect(screen.getByRole('alert')).toHaveTextContent(/unable|couldn't|cannot|no results/i);
    // No per-hole dropdowns in the fallback.
    expect(screen.queryAllByRole('combobox')).toHaveLength(0);
  });

  it('renders the fallback when marked_image_url is null/empty', async () => {
    vi.spyOn(api, 'getScoringJob').mockResolvedValue(
      makeJob({ marked_image_url: null })
    );
    renderAt('/results/abc');
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });

  it('changes a hole score via the dropdown (corrections flow into the accept payload on Accept)', async () => {
    vi.spyOn(api, 'getScoringJob').mockResolvedValue(makeJob());
    const acceptSpy = vi.spyOn(api, 'acceptResult').mockResolvedValue({
      result_id: 'r1', source_job: 'abc', target_type: 'air_pistol',
      holes: [], score_average: 9.0,
    });
    renderAt('/results/abc');
    await waitFor(() => expect(screen.getAllByRole('combobox')).toHaveLength(6));
    // The 2 hole-correction selects are the first two comboboxes.
    const holeSelects = screen.getAllByRole('combobox').slice(0, 2);
    await userEvent.selectOptions(holeSelects[0], '9');
    // Accept → acceptResult called with the corrected score (9 for hole 0).
    await userEvent.click(screen.getByRole('button', { name: /accept result/i }));
    await waitFor(() => expect(acceptSpy).toHaveBeenCalledTimes(1));
    const payload = acceptSpy.mock.calls[0][1];
    expect(payload.holes[0].score).toBe(9);
  });

  it('Accept calls acceptResult with the params + corrected holes, then navigates to /dashboard', async () => {
    vi.spyOn(api, 'getScoringJob').mockResolvedValue(makeJob({
      caliber_hint: '9x19mm', distance: 25, weapon_type: 'sport_pistol',
    }));
    const acceptSpy = vi.spyOn(api, 'acceptResult').mockResolvedValue({
      result_id: 'r1', source_job: 'abc', target_type: 'air_pistol',
      holes: [], score_average: 8.5,
    });
    const { getPath } = renderAtWithProbe('/results/abc');
    await waitFor(() => expect(screen.getByRole('button', { name: /accept result/i })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /accept result/i }));
    await waitFor(() => expect(acceptSpy).toHaveBeenCalledTimes(1));
    expect(acceptSpy.mock.calls[0][0]).toBe('abc'); // jobId from the route
    const payload = acceptSpy.mock.calls[0][1];
    expect(payload.target_type).toBe('air_pistol');
    expect(payload.caliber_hint).toBe('9x19mm');
    expect(payload.distance).toBe(25);
    expect(payload.weapon_type).toBe('sport_pistol');
    expect(getPath()).toBe('/dashboard');
  });

  it('Reject navigates to /dashboard WITHOUT calling acceptResult', async () => {
    vi.spyOn(api, 'getScoringJob').mockResolvedValue(makeJob());
    const acceptSpy = vi.spyOn(api, 'acceptResult').mockResolvedValue({
      result_id: 'r1', source_job: 'abc', target_type: 'air_pistol',
      holes: [], score_average: 8.5,
    });
    const { getPath } = renderAtWithProbe('/results/abc');
    await waitFor(() => expect(screen.getByRole('button', { name: /reject result/i })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /reject result/i }));
    expect(acceptSpy).not.toHaveBeenCalled();
    expect(getPath()).toBe('/dashboard');
  });

  it('shows a role=alert when acceptResult fails', async () => {
    vi.spyOn(api, 'getScoringJob').mockResolvedValue(makeJob());
    vi.spyOn(api, 'acceptResult').mockRejectedValue(new Error('POST /v1/scoring/results failed: 409'));
    renderAt('/results/abc');
    await waitFor(() => expect(screen.getByRole('button', { name: /accept result/i })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /accept result/i }));
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/accept failed/i);
    });
  });
});
