// ui-chores Phase 3: ScoreRow icon-only action-button contract. Each of the
// three action buttons (Preview / Modify / Delete) is ICON-ONLY — the visible
// text was dropped (icons replace it, per the change's actual intent). The
// accessible name comes from each button's aria-label (preserved verbatim),
// so screen readers / tooltip-hover still announce "Preview/Modify/Delete
// score from <date>". The Preview icon is an <img> (the immutable target.svg,
// alt="" decorative); Modify/Delete use react-icons inline SVGs (aria-hidden).
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

  it('renders an icon-only Preview button (img glyph, no visible text)', () => {
    render(<ScoreRow row={makeRow()} onModified={() => {}} onDeleted={() => {}} />);
    const preview = screen.getByRole('button', { name: /preview score from/i });
    const img = preview.querySelector('img');
    expect(img).not.toBeNull();
    // ui-chores post-impl-review correction: text dropped — icon replaces it.
    expect(preview).not.toHaveTextContent(/preview/i);
    expect(preview.textContent?.trim()).toBe('');
  });

  it('renders an icon-only Modify button (inline svg, no visible text)', () => {
    render(<ScoreRow row={makeRow()} onModified={() => {}} onDeleted={() => {}} />);
    const modify = screen.getByRole('button', { name: /modify score from/i });
    expect(modify.querySelector('svg')).not.toBeNull();
    expect(modify).not.toHaveTextContent(/modify/i);
    expect(modify.textContent?.trim()).toBe('');
  });

  it('renders an icon-only Delete button (inline svg, no visible text)', () => {
    render(<ScoreRow row={makeRow()} onModified={() => {}} onDeleted={() => {}} />);
    const del = screen.getByRole('button', { name: /delete score from/i });
    expect(del.querySelector('svg')).not.toBeNull();
    expect(del).not.toHaveTextContent(/delete/i);
    expect(del.textContent?.trim()).toBe('');
  });

  it('marks the Modify/Delete icons aria-hidden (decorative; label carries meaning)', () => {
    render(<ScoreRow row={makeRow()} onModified={() => {}} onDeleted={() => {}} />);
    const modify = screen.getByRole('button', { name: /modify score from/i });
    const del = screen.getByRole('button', { name: /delete score from/i });
    expect(modify.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
    expect(del.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
  });
});
