// ui-chores Phase 3: ScoreRow icon contract. Each of the three action buttons
// (Preview / Modify / Delete) gets a leading icon node alongside its text
// label; existing aria-labels stay put so screen readers keep announcing the
// descriptive action, not the decorative glyph. The Preview icon is an <img>
// (the immutable target.svg); Modify/Delete use react-icons inline SVGs.
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ScoreRow } from './ScoreRow';
import type { ResultSummary } from '../api';

function makeRow(overrides: Partial<ResultSummary> = {}): ResultSummary {
  return {
    result_id: 'r-1',
    source_job: 'j-1',
    created_at: '2026-08-05T12:00:00Z',
    score_average: 8.5,
    hole_count: 10,
    target_type: '10m',
    ...overrides,
  };
}

describe('ScoreRow action buttons', () => {
  it('preserves the descriptive aria-label on each button', () => {
    render(<ScoreRow row={makeRow()} onModified={() => {}} onDeleted={() => {}} />);
    // The date label is the ISO date prefix.
    expect(screen.getByRole('button', { name: /preview score from 2026-08-05/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /modify score from 2026-08-05/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /delete score from 2026-08-05/i })).toBeInTheDocument();
  });

  it('renders a leading icon node (img or svg) inside the Preview button', () => {
    render(<ScoreRow row={makeRow()} onModified={() => {}} onDeleted={() => {}} />);
    const preview = screen.getByRole('button', { name: /preview score from/i });
    const img = preview.querySelector('img');
    expect(img).not.toBeNull();
    expect(preview).toHaveTextContent(/preview/i);
  });

  it('renders a leading inline-svg icon inside the Modify button', () => {
    render(<ScoreRow row={makeRow()} onModified={() => {}} onDeleted={() => {}} />);
    const modify = screen.getByRole('button', { name: /modify score from/i });
    expect(modify.querySelector('svg')).not.toBeNull();
    expect(modify).toHaveTextContent(/modify/i);
  });

  it('renders a leading inline-svg icon inside the Delete button', () => {
    render(<ScoreRow row={makeRow()} onModified={() => {}} onDeleted={() => {}} />);
    const del = screen.getByRole('button', { name: /delete score from/i });
    expect(del.querySelector('svg')).not.toBeNull();
    expect(del).toHaveTextContent(/delete/i);
  });

  it('marks the Modify/Delete icons aria-hidden (decorative; label carries meaning)', () => {
    render(<ScoreRow row={makeRow()} onModified={() => {}} onDeleted={() => {}} />);
    const modify = screen.getByRole('button', { name: /modify score from/i });
    const del = screen.getByRole('button', { name: /delete score from/i });
    expect(modify.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
    expect(del.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
  });
});
