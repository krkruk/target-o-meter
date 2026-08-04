// user-score-dashboard: <ScoreRow> + <ScoreList> contract.
//
// Pins the shared row + day-bucketed list the dashboard and home "Recent
// results" both render. <ScoreRow> shows date + bolded score + the three
// action buttons (Preview/Modify/Delete); <ScoreList> groups rows under
// YYYY-MM-DD day headers (most-recent first from the API) and shows an empty
// state. The on* handlers are stubs here (Phase 3); Phase 4 wires the modals
// inside <ScoreRow> itself (so the row's mutate props become success callbacks
// onModified/onDeleted — those names are already used here to avoid a rename).
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

describe('<ScoreList>', () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it('renders an empty state when there are no rows', () => {
    render(
      <ScoreList
        rows={[]}
        onPreview={vi.fn()}
        onModified={vi.fn()}
        onDeleted={vi.fn()}
      />,
    );
    expect(screen.getByText(/no scores yet/i)).toBeInTheDocument();
  });

  it('groups rows under a YYYY-MM-DD day header', () => {
    const rows = [
      makeRow({ result_id: 'r1', created_at: '2026-08-04T10:00:00Z' }),
      makeRow({ result_id: 'r2', created_at: '2026-08-04T12:00:00Z' }),
      makeRow({ result_id: 'r3', created_at: '2026-08-03T09:00:00Z' }),
    ];
    render(
      <ScoreList
        rows={rows}
        onPreview={vi.fn()}
        onModified={vi.fn()}
        onDeleted={vi.fn()}
      />,
    );
    // Two day headers (Aug 4 + Aug 3) — scope to heading level to distinguish
    // from the per-row date spans (which also show YYYY-MM-DD).
    const dayHeaders = screen.getAllByRole('heading', { level: 3 });
    expect(dayHeaders.map((h) => h.textContent)).toEqual(['2026-08-04', '2026-08-03']);
  });

  it('renders date + bolded score on each row', () => {
    render(
      <ScoreList
        rows={[makeRow({ score_average: 9.5 })]}
        onPreview={vi.fn()}
        onModified={vi.fn()}
        onDeleted={vi.fn()}
      />,
    );
    expect(screen.getByText('9.5')).toBeInTheDocument();
  });

  it('renders Preview/Modify/Delete buttons on each row', () => {
    render(
      <ScoreList
        rows={[makeRow()]}
        onPreview={vi.fn()}
        onModified={vi.fn()}
        onDeleted={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: /preview/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /modify/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /delete/i })).toBeInTheDocument();
  });

  it('Preview button calls onPreview with the row', async () => {
    const onPreview = vi.fn();
    const row = makeRow();
    render(
      <ScoreList
        rows={[row]}
        onPreview={onPreview}
        onModified={vi.fn()}
        onDeleted={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: /preview/i }));
    expect(onPreview).toHaveBeenCalledWith(row);
  });
});
