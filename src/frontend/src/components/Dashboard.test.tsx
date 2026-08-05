// S-02 Phase 7 + S-03: Dashboard contract. Single-screen CSS Grid dashboard —
// hero stats, add-photos button (branches to /capture on mobile, /upload on
// desktop), results list, and a daily-average recharts chart. S-03 swapped the
// mocked fixtures for real getAggregations() calls (loading → role=status,
// error → role=alert). Accessibility-first: each region carries role="region"
// + aria-label; the chart wrapper is role="img" with a summarizing aria-label.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import * as api from '../api';
import { Dashboard } from './Dashboard';

// jsdom doesn't implement window.matchMedia — the dashboard uses it to branch
// the add-photos button between /capture (mobile, <=760px) and /upload
// (desktop). Each test installs the matchMedia stub for the viewport it tests.
function installMatchMedia(maxWidth: number) {
  const desktop = maxWidth > 760;
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: desktop
      ? !query.includes('max-width')
      : query.includes('max-width: 760px'),
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
  return desktop;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/*" element={<Dashboard />} />
        <Route path="/capture" element={<div>capture-route-sentinel</div>} />
        <Route path="/upload" element={<div>upload-route-sentinel</div>} />
      </Routes>
    </MemoryRouter>
  );
}

describe('Dashboard', () => {
  beforeEach(() => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }));
    // S-03: default the aggregation spy to a populated payload so the
    // structural tests (regions, add-photos, chart) don't depend on fetch.
    // Per-test overrides replace this with their own spy.
    vi.spyOn(api, 'getAggregations').mockResolvedValue({
      hero: { total_shots: 19, last_session_average: 6.0, best_result: 10.0 },
      recent: [{
        result_id: 'r1', source_job: 'job-1', created_at: '2026-07-28T12:00:00Z',
        score_average: 8.4, hole_count: 5, target_type: 'air_pistol',
      }],
      daily_averages: [{ date: '2026-07-28', average: 8.4 }],
    });
    // user-score-dashboard: Dashboard's "Recent results" now also calls
    // getScores — default it to empty so structural tests don't hit real fetch.
    vi.spyOn(api, 'getScores').mockResolvedValue({
      items: [], page: 1, page_size: 20, total: 0, total_pages: 0,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the four named regions (hero, add-photos, results, chart)', async () => {
    renderAt('/dashboard');
    // The hero region renders after getAggregations resolves; results + chart
    // are always present (they handle null props). Await the hero region.
    expect(await screen.findByRole('region', { name: /hero stats/i })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: /results/i })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: /daily average/i })).toBeInTheDocument();
    // The add-photos button is the affordance that branches capture/upload.
    expect(screen.getByRole('button', { name: /add photos/i })).toBeInTheDocument();
  });

  it('routes to /upload when the add-photos button is activated on desktop', async () => {
    installMatchMedia(1920); // desktop
    let currentPath = '';
    function Probe() {
      currentPath = useLocation().pathname;
      return null;
    }
    render(
      <MemoryRouter initialEntries={['/dashboard']}>
        <Probe />
        <Routes>
          <Route path="/*" element={<Dashboard />} />
          <Route path="/upload" element={<div>upload-sentinel</div>} />
          <Route path="/capture" element={<div>capture-sentinel</div>} />
        </Routes>
      </MemoryRouter>
    );
    expect(currentPath).toBe('/dashboard');
    await userEvent.click(screen.getByRole('button', { name: /add photos/i }));
    expect(currentPath).toBe('/upload');
  });

  it('routes to /capture when the add-photos button is activated on mobile (<=760px)', async () => {
    installMatchMedia(390); // mobile
    let currentPath = '';
    function Probe() {
      currentPath = useLocation().pathname;
      return null;
    }
    render(
      <MemoryRouter initialEntries={['/dashboard']}>
        <Probe />
        <Routes>
          <Route path="/*" element={<Dashboard />} />
          <Route path="/upload" element={<div>upload-sentinel</div>} />
          <Route path="/capture" element={<div>capture-sentinel</div>} />
        </Routes>
      </MemoryRouter>
    );
    await userEvent.click(screen.getByRole('button', { name: /add photos/i }));
    expect(currentPath).toBe('/capture');
  });

  it('renders the daily-average chart with an accessible role=img + summary', () => {
    renderAt('/dashboard');
    // recharts SVGs aren't screen-reader-friendly; the wrapper carries role=img
    // + an aria-label summarizing the data.
    const chart = screen.getByRole('img', { name: /daily average/i });
    expect(chart).toBeInTheDocument();
  });

  it('renders hero stat values from getAggregations', async () => {
    // S-03: the dashboard fetches real aggregations (not the S-02 mocked fixture).
    vi.spyOn(api, 'getAggregations').mockResolvedValue({
      hero: { total_shots: 19, last_session_average: 6.0, best_result: 10.0 },
      recent: [],
      daily_averages: [],
    });
    renderAt('/dashboard');
    // The hero region populates with the fetched total_shots (19).
    const hero = await screen.findByRole('region', { name: /hero stats/i });
    expect(hero).toHaveTextContent('19');
  });

  it('renders results-list rows from getScores', async () => {
    // user-score-dashboard: the "Recent results" region now sources its rows
    // from getScores({page:1, page_size:20}) (was getAggregations().recent).
    vi.spyOn(api, 'getAggregations').mockResolvedValue({
      hero: { total_shots: 0, last_session_average: null, best_result: null },
      recent: [],
      daily_averages: [],
    });
    vi.spyOn(api, 'getScores').mockResolvedValue({
      items: [{
        result_id: 'r1', source_job: 'job-1', created_at: '2026-07-28T12:00:00Z',
        score_average: 8.4, hole_count: 5, target_type: 'air_pistol',
      }],
      page: 1, page_size: 20, total: 1, total_pages: 1,
    });
    renderAt('/dashboard');
    const results = await screen.findByRole('region', { name: /results/i });
    // The fetched recent row renders (date + bolded score_average).
    expect(results).toHaveTextContent('8.4');
  });

  it('renders a role=status loading state before aggregations resolve', () => {
    // Never-resolving spy so the loading state stays mounted.
    vi.spyOn(api, 'getAggregations').mockReturnValue(new Promise(() => {}));
    renderAt('/dashboard');
    expect(screen.getByRole('status', { name: /loading hero stats/i })).toBeInTheDocument();
  });

  it('renders a role=alert error state when getAggregations fails', async () => {
    vi.spyOn(api, 'getAggregations').mockRejectedValue(new Error('GET /v1/scores/aggregations failed: 500'));
    renderAt('/dashboard');
    expect(await screen.findByRole('alert')).toHaveTextContent(/unable to load dashboard/i);
  });

  it('refetches aggregations when a row is modified or deleted (impl-review F2)', async () => {
    // The row owns the modal state and bubbles success via onModified/onDeleted.
    // After either, the home Dashboard must also refetch getAggregations() so
    // the hero (best_result/last_session_average) + daily-averages chart don't
    // stay stale. This surfaces as a second getAggregations call per mutation.
    const aggSpy = vi.spyOn(api, 'getAggregations').mockResolvedValue({
      hero: { total_shots: 5, last_session_average: 8.0, best_result: 9.0 },
      recent: [],
      daily_averages: [{ date: '2026-07-28', average: 8.0 }],
    });
    // Surface a row that renders a Modify + Delete button so we can drive the
    // success callbacks through the real <ScoreRow> wiring.
    vi.spyOn(api, 'getScores').mockResolvedValue({
      items: [{
        result_id: 'r1', source_job: 'job-1', created_at: '2026-07-28T12:00:00Z',
        score_average: 8.0, hole_count: 5, target_type: 'air_pistol',
      }],
      page: 1, page_size: 20, total: 1, total_pages: 1,
    });
    renderAt('/dashboard');
    // Initial mount: one aggregations call (plus the getScores call).
    await screen.findByRole('region', { name: /hero stats/i });
    const initialAggCalls = aggSpy.mock.calls.length;
    expect(initialAggCalls).toBeGreaterThanOrEqual(1);

    // Drive onModified: ScoreRow's Modify button opens ModifyModal which calls
    // updateScore + onModified(). To stay focused on the refetch contract (not
    // the modal's network plumbing), invoke the success callback directly by
    // clicking Modify then reading the spy count after the modal's PATCH.
    // The ModifyModal fetches getScore on open, so stub it.
    vi.spyOn(api, 'getScore').mockResolvedValue({
      result_id: 'r1', source_job: 'job-1', created_at: '2026-07-28T12:00:00Z',
      score_average: 8.0, target_type: 'air_pistol',
      holes: [{ x: 1, y: 2, score: 8, confidence: 0.9 }],
    });
    const updateSpy = vi.spyOn(api, 'updateScore').mockResolvedValue({
      result_id: 'r1', source_job: 'job-1', created_at: '2026-07-28T12:00:00Z',
      score_average: 9.0, target_type: 'air_pistol',
      holes: [{ x: 1, y: 2, score: 9, confidence: 0.9 }],
    });

    await userEvent.click(await screen.findByRole('button', { name: /modify/i }));
    // Modal is open; submit. The form's Modify submit calls updateScore → onModified.
    const modifyBtns = screen.getAllByRole('button', { name: /^modify$/i });
    await userEvent.click(modifyBtns[modifyBtns.length - 1]);
    await vi.waitFor(() => { expect(updateSpy).toHaveBeenCalled(); });

    // After Modify success: getAggregations was called again.
    await vi.waitFor(() => {
      expect(aggSpy.mock.calls.length).toBeGreaterThan(initialAggCalls);
    });
    const afterModifyAggCalls = aggSpy.mock.calls.length;

    // Drive onDeleted via the Delete button → confirm.
    const deleteSpy = vi.spyOn(api, 'deleteScore').mockResolvedValue(undefined);
    await userEvent.click(await screen.findByRole('button', { name: /delete/i }));
    await userEvent.click(screen.getByRole('button', { name: /delete permanently/i }));
    await vi.waitFor(() => { expect(deleteSpy).toHaveBeenCalled(); });

    // After Delete success: getAggregations was called yet again.
    await vi.waitFor(() => {
      expect(aggSpy.mock.calls.length).toBeGreaterThan(afterModifyAggCalls);
    });
  });
});
