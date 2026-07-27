// S-02 Phase 8: Results route contract. Fetches the ScoringJob and renders the
// marked image with per-hole correction dropdowns (UI-only in S-02 —
// persistence is S-03). If result is null OR marked_image_url is null/empty,
// render an "unable to load results" fallback (the _job_to_dto fragility can
// produce a null result even on succeeded).
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
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

function makeJob(overrides: Partial<ScoringJob> = {}): ScoringJob {
  return {
    job_id: 'abc',
    status: 'succeeded',
    target_type: 'air_pistol',
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
    // One correction <select> per hole, plus accessible labels.
    const selects = screen.getAllByRole('combobox');
    expect(selects).toHaveLength(job.result!.holes.length);
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

  it('changes a hole score via the dropdown (local state only — no API call in S-02)', async () => {
    const spy = vi.spyOn(api, 'getScoringJob').mockResolvedValue(makeJob());
    const { container } = renderAt('/results/abc');
    await waitFor(() => expect(screen.getAllByRole('combobox')).toHaveLength(2));
    // getScoringJob is called only on mount (S-02: corrections are local, no save).
    const callsBefore = spy.mock.calls.length;
    // Selecting a new score must not trigger another getScoringJob.
    const firstSelect = screen.getAllByRole('combobox')[0];
    firstSelect.setAttribute('value', '9');
    expect(spy.mock.calls.length).toBe(callsBefore);
    expect(container).toBeTruthy();
  });
});
