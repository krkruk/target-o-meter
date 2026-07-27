// S-02 Phase 8: Waiting route contract. Polls getScoringJob(jobId) on an
// interval until a terminal status, then navigates to /results/:jobId on
// success or shows a role="alert" error on failure. The "always resolves"
// guarantee comes from the BFF's reap_stuck_jobs() on every poll (Phase 4.1).
//
// State machine: queued (initial) -> running (spinner) -> succeeded (navigate)
//                                                    \-> failed (role="alert").
//
// Uses REAL timers with a tiny injected poll interval (Waiting takes pollMs as
// a prop). Fake timers race the async getScoringJob resolution against React's
// commit under vitest; real timers + a 10ms poll keep the test deterministic
// without that conflict.
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import * as api from '../api';
import type { ScoringJob } from '../api';
import { Waiting } from './Waiting';

function makeJob(overrides: Partial<ScoringJob> = {}): ScoringJob {
  return {
    job_id: 'abc',
    status: 'queued',
    target_type: 'air_pistol',
    ...overrides,
  };
}

function renderAt(path: string) {
  let currentPath = path;
  function Probe() {
    currentPath = useLocation().pathname;
    return null;
  }
  const utils = render(
    <MemoryRouter initialEntries={[path]}>
      <Probe />
      <Routes>
        {/* pollMs=10 keeps the test fast without fake timers. */}
        <Route path="/waiting/:jobId" element={<Waiting pollMs={10} />} />
        <Route path="/results/:jobId" element={<div>results-sentinel</div>} />
      </Routes>
    </MemoryRouter>
  );
  return { ...utils, getPath: () => currentPath };
}

describe('Waiting', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows a polling status indicator (role=status) while queued/running', async () => {
    vi.spyOn(api, 'getScoringJob').mockResolvedValue(makeJob({ status: 'running' }));
    renderAt('/waiting/abc');
    await waitFor(() => {
      expect(screen.getByRole('status')).toBeInTheDocument();
    });
  });

  it('navigates to /results/:jobId once status is succeeded', async () => {
    const spy = vi.spyOn(api, 'getScoringJob');
    // First poll: running. Second poll: succeeded.
    spy
      .mockResolvedValueOnce(makeJob({ status: 'running' }))
      .mockResolvedValueOnce(
        makeJob({
          status: 'succeeded',
          result: { holes: [], target_type: 'air_pistol', detector_name: 'mock' },
        }),
      );
    const { getPath } = renderAt('/waiting/abc');
    await waitFor(() => expect(getPath()).toBe('/results/abc'));
  });

  it('renders role=alert on a failed job', async () => {
    vi.spyOn(api, 'getScoringJob').mockResolvedValue(
      makeJob({ status: 'failed', error: 'detector blew up' }),
    );
    renderAt('/waiting/abc');
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert')).toHaveTextContent(/detector blew up|failed/i);
  });

  it('stops polling after a terminal status (no further getScoringJob calls)', async () => {
    const spy = vi.spyOn(api, 'getScoringJob');
    spy
      .mockResolvedValueOnce(makeJob({ status: 'running' }))
      .mockResolvedValue(
        makeJob({
          status: 'succeeded',
          result: { holes: [], target_type: 'air_pistol', detector_name: 'mock' },
        }),
      );
    renderAt('/waiting/abc');
    // Wait for the terminal poll + navigation.
    await waitFor(() => expect(spy.mock.calls.length).toBeGreaterThanOrEqual(2));
    const callsAfterTerminal = spy.mock.calls.length;
    // Wait well past several poll intervals — no new calls should land.
    await new Promise((r) => setTimeout(r, 60));
    expect(spy.mock.calls.length).toBe(callsAfterTerminal);
  });
});
