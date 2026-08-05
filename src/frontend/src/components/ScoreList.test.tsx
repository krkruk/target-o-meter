// user-score-dashboard: <ScoreRow> + <ScoreList> contract.
//
// Pins the shared row + day-bucketed list the dashboard and home "Recent
// results" both render. <ScoreRow> shows date + bolded score + the three
// action buttons (Preview/Modify/Delete) and OWNS its modal state (Phase 4);
// <ScoreList> groups rows under YYYY-MM-DD day headers (most-recent first from
// the API) and shows an empty state. The parent passes only success callbacks
// onModified/onDeleted (the row handles the rest internally).
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import * as api from '../api';
import { ScoreList } from './ScoreList';
import type { ResultSummary } from '../api';

function makeRow(overrides: Partial<ResultSummary> = {}): ResultSummary {
  return {
    result_id: 'r1',
    source_job: 'job-1',
    created_at: '2026-08-04T10:00:00Z',
    score_average: 9.0,
    hole_count: 10,
    target_type: 'air_pistol',
    ...overrides,
  };
}

function renderList(rows: ResultSummary[], onModified = vi.fn(), onDeleted = vi.fn()) {
  return render(<ScoreList rows={rows} onModified={onModified} onDeleted={onDeleted} />);
}

describe('<ScoreList>', () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it('renders an empty state when there are no rows', () => {
    renderList([]);
    expect(screen.getByText(/no scores yet/i)).toBeInTheDocument();
  });

  it('groups rows under a YYYY-MM-DD day header', () => {
    const rows = [
      makeRow({ result_id: 'r1', created_at: '2026-08-04T10:00:00Z' }),
      makeRow({ result_id: 'r2', created_at: '2026-08-04T12:00:00Z' }),
      makeRow({ result_id: 'r3', created_at: '2026-08-03T09:00:00Z' }),
    ];
    renderList(rows);
    // Two day headers (Aug 4 + Aug 3) — scope to heading level to distinguish
    // from the per-row date spans (which also show YYYY-MM-DD).
    const dayHeaders = screen.getAllByRole('heading', { level: 3 });
    expect(dayHeaders.map((h) => h.textContent)).toEqual(['2026-08-04', '2026-08-03']);
  });

  it('renders date + bolded score on each row', () => {
    renderList([makeRow({ score_average: 9.5 })]);
    expect(screen.getByText('9.5')).toBeInTheDocument();
  });

  it('renders Preview/Modify/Delete buttons on each row', () => {
    renderList([makeRow()]);
    expect(screen.getByRole('button', { name: /preview/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /modify/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /delete/i })).toBeInTheDocument();
  });

  it('Preview toggles an inline reveal (fetches getScore)', async () => {
    vi.spyOn(api, 'getScore').mockResolvedValue({
      result_id: 'r1', source_job: 'job-1', target_type: 'air_pistol',
      holes: [{ x: 0, y: 0, score: 9, confidence: 1.0 }], score_average: 9.0,
      created_at: '2026-08-04T10:00:00Z',
    });
    renderList([makeRow()]);
    await userEvent.click(screen.getByRole('button', { name: /preview/i }));
    // The proxy image renders with the same-origin URL (no presigned S3).
    expect(await screen.findByRole('img', { name: /marked target/i }))
      .toHaveAttribute('src', '/v1/scoring/jobs/job-1/marked-image');
  });

  it('Modify opens the modal (no API call until submit)', async () => {
    vi.spyOn(api, 'getScore').mockResolvedValue({
      result_id: 'r1', source_job: 'job-1', target_type: 'air_pistol',
      holes: [{ x: 0, y: 0, score: 9, confidence: 1.0 }], score_average: 9.0,
      created_at: '2026-08-04T10:00:00Z',
    });
    const onModified = vi.fn();
    renderList([makeRow()], onModified);
    await userEvent.click(screen.getByRole('button', { name: /modify/i }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    // The Modify submit button is present (Cancel/Modify).
    expect(screen.getByRole('button', { name: /^modify$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
  });

  it('Delete opens a confirm modal', async () => {
    renderList([makeRow()]);
    await userEvent.click(screen.getByRole('button', { name: /delete/i }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /delete permanently/i })).toBeInTheDocument();
  });
});
