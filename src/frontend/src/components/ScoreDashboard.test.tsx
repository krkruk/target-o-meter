// user-score-dashboard: <ScoreDashboard> (read-only, Phase 3) contract.
//
// Pins the page-size dropdown (10/20/30/50, default 20), the Prev/Next pager
// (disabled at bounds), and the loading/error states. The row action handlers
// are stubs in this phase (Phase 4 wires the modals). Mirrors the
// AdminUsersPage read-only test shape: mock getScores, render, assert.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import * as api from '../api';
import { ScoreDashboard } from './ScoreDashboard';
import type { ScoreList } from '../api';

function makeList(overrides: Partial<ScoreList> = {}): ScoreList {
  return {
    items: [],
    page: 1,
    page_size: 20,
    total: 0,
    total_pages: 0,
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ScoreDashboard />
    </MemoryRouter>,
  );
}

describe('<ScoreDashboard> (read-only)', () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it('renders a loading state, then the list', async () => {
    vi.spyOn(api, 'getScores').mockResolvedValue(makeList());
    renderPage();
    expect(screen.getByRole('status')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    expect(api.getScores).toHaveBeenCalledWith({ page: 1, page_size: 20 });
  });

  it('renders the page-size dropdown defaulting to 20', async () => {
    vi.spyOn(api, 'getScores').mockResolvedValue(makeList());
    renderPage();
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    const select = screen.getByRole('combobox', { name: /page size/i }) as HTMLSelectElement;
    expect(select.value).toBe('20');
  });

  it('changing the page-size dropdown resets page=1 and re-fetches', async () => {
    vi.spyOn(api, 'getScores').mockResolvedValue(makeList({ total_pages: 3 }));
    renderPage();
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());

    // Navigate to page 2 first (so we can assert the dropdown resets it).
    await userEvent.click(screen.getByRole('button', { name: /next/i }));
    await waitFor(() => {
      const calls = vi.mocked(api.getScores).mock.calls;
      const lastCall = calls[calls.length - 1];
      expect(lastCall?.[0]).toEqual({ page: 2, page_size: 20 });
    });

    // Change page size → resets to page 1 with the new size.
    const select = screen.getByRole('combobox', { name: /page size/i });
    await userEvent.selectOptions(select, '10');
    await waitFor(() => {
      const calls = vi.mocked(api.getScores).mock.calls;
      const lastCall = calls[calls.length - 1];
      expect(lastCall?.[0]).toEqual({ page: 1, page_size: 10 });
    });
  });

  it('disables Prev on page 1 and Next on the last page', async () => {
    vi.spyOn(api, 'getScores').mockResolvedValue(makeList({ page: 1, total_pages: 2 }));
    renderPage();
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());

    expect(screen.getByRole('button', { name: /previous/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /next/i })).toBeEnabled();

    await userEvent.click(screen.getByRole('button', { name: /next/i }));
    vi.spyOn(api, 'getScores').mockResolvedValue(makeList({ page: 2, total_pages: 2 }));
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /previous/i })).toBeEnabled();
    });
    expect(screen.getByRole('button', { name: /next/i })).toBeDisabled();
  });

  it('renders an error state when the fetch fails', async () => {
    vi.spyOn(api, 'getScores').mockRejectedValue(new Error('boom'));
    renderPage();
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
  });
});
