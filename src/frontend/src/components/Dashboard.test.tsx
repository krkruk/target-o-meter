// S-02 Phase 7: Dashboard contract. Single-screen CSS Grid dashboard — hero
// stats, add-photos button (branches to /capture on mobile, /upload on
// desktop), results list, and a daily-average recharts chart. The chart +
// results use mocked fixtures (aggregation is S-03). Accessibility-first:
// each region carries role="region" + aria-label; the chart wrapper is
// role="img" with a summarizing aria-label (recharts SVGs aren't screen-
// reader-friendly by default).
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
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
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the four named regions (hero, add-photos, results, chart)', () => {
    renderAt('/dashboard');
    // Each region is an accessible landmark with a discernible aria-label.
    expect(screen.getByRole('region', { name: /hero stats/i })).toBeInTheDocument();
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

  it('renders hero stat values from the mocked fixture', () => {
    renderAt('/dashboard');
    const hero = screen.getByRole('region', { name: /hero stats/i });
    // The mocked fixture carries deterministic values (total shots, last
    // session, best result). Assert the region is populated (not empty).
    expect(hero.textContent?.trim().length).toBeGreaterThan(0);
  });

  it('renders results-list rows from the mocked fixture', () => {
    renderAt('/dashboard');
    const results = screen.getByRole('region', { name: /results/i });
    // The mocked fixture seeds at least one result row.
    expect(results.textContent?.trim().length).toBeGreaterThan(0);
  });
});
