// S-04 Phase 3: AdminUsersPage (read-only list) contract.
//
// Covers the search + pagination render, the ban-status chips, the 403
// handling (non-owner hitting /admin), and the loading/empty/error states.
// Mutations (ban/unban/delete) land in Phase 4.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import * as api from '../api';
import { AdminUsersPage } from './AdminUsersPage';
import type { AdminUserList, AdminUser } from '../api';

function makeAdminUser(overrides: Partial<AdminUser> = {}): AdminUser {
  return {
    user_uuid: 'uuid-1',
    sub: 'auth0|alice',
    nick: 'alice',
    has_set_nick: true,
    is_owner: false,
    ban: { is_banned: false, reason: null, banned_until: null, lifted_at: null, has_prior_ban: false },
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <AdminUsersPage />
    </MemoryRouter>,
  );
}

describe('AdminUsersPage (read-only)', () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it('renders a loading state, then the list', async () => {
    const users: AdminUserList = {
      items: [makeAdminUser()], page: 1, page_size: 20, total: 1, total_pages: 1,
    };
    const spy = vi.spyOn(api, 'getAdminUsers').mockResolvedValue(users);

    renderPage();
    expect(screen.getByRole('status')).toBeInTheDocument();

    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    expect(spy).toHaveBeenCalled();
    expect(screen.getByText('alice')).toBeInTheDocument();
    expect(screen.getByText('auth0|alice')).toBeInTheDocument();
  });

  it('renders a red "Active ban" chip for a banned user', async () => {
    const banned = makeAdminUser({
      nick: 'banned-alice', sub: 'auth0|banned',
      ban: { is_banned: true, reason: 'spamming', banned_until: '2099-01-01T00:00:00Z', lifted_at: null, has_prior_ban: true },
    });
    vi.spyOn(api, 'getAdminUsers').mockResolvedValue({
      items: [banned], page: 1, page_size: 20, total: 1, total_pages: 1,
    });
    renderPage();
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    expect(screen.getByText(/active ban/i)).toBeInTheDocument();
  });

  it('renders a grey "Banned before" chip for a prior-ban user', async () => {
    const prior = makeAdminUser({
      nick: 'reformed', sub: 'auth0|prior',
      ban: { is_banned: false, reason: 'old', banned_until: null, lifted_at: '2020-01-01T00:00:00Z', has_prior_ban: true },
    });
    vi.spyOn(api, 'getAdminUsers').mockResolvedValue({
      items: [prior], page: 1, page_size: 20, total: 1, total_pages: 1,
    });
    renderPage();
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    expect(screen.getByText(/banned before/i)).toBeInTheDocument();
  });

  it('shows an empty state when there are no users', async () => {
    vi.spyOn(api, 'getAdminUsers').mockResolvedValue({
      items: [], page: 1, page_size: 20, total: 0, total_pages: 0,
    });
    renderPage();
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    expect(screen.getByText(/no users match/i)).toBeInTheDocument();
  });

  it('shows an "Owner privileges required" message on 403', async () => {
    vi.spyOn(api, 'getAdminUsers').mockRejectedValue(new api.HttpError(403, 'forbidden'));
    renderPage();
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    expect(screen.getByText(/owner privileges required/i)).toBeInTheDocument();
  });

  it('debounces the search input (~250ms)', async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(api, 'getAdminUsers').mockResolvedValue({
      items: [], page: 1, page_size: 20, total: 0, total_pages: 0,
    });
    renderPage();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    const search = screen.getByRole('searchbox', { name: /search users/i });
    await user.type(search, 'ali');
    // Immediately after typing, only the initial mount fetch has run.
    const callsAfterType = spy.mock.calls.length;
    expect(callsAfterType).toBe(1);

    // After the debounce window elapses, the typed query fires.
    await waitFor(
      () => expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'ali' })),
      { timeout: 2000 },
    );
  }, 10000);

  it('renders a pager when total_pages > 1 and navigates to the next page', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'getAdminUsers').mockImplementation(async (params = {}) => {
      const p = params.page ?? 1;
      return {
        items: p === 1
          ? [makeAdminUser({ nick: 'p1-user', sub: 'auth0|p1' })]
          : [makeAdminUser({ nick: 'p2-user', sub: 'auth0|p2', user_uuid: 'uuid-2' })],
        page: p, page_size: 20, total: 25, total_pages: 2,
      };
    });
    renderPage();
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    expect(screen.getByText(/page 1 of 2/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /next/i }));
    await waitFor(() => expect(screen.getByText(/page 2 of 2/i)).toBeInTheDocument());
    expect(screen.getByText('p2-user')).toBeInTheDocument();
  });

  it('renders no action buttons on the owner row (owner is always read-only)', async () => {
    const owner = makeAdminUser({ nick: 'owner', sub: 'auth0|owner', is_owner: true });
    const plain = makeAdminUser({ nick: 'plain', sub: 'auth0|plain', user_uuid: 'uuid-2' });
    vi.spyOn(api, 'getAdminUsers').mockResolvedValue({
      items: [owner, plain], page: 1, page_size: 20, total: 2, total_pages: 1,
    });
    renderPage();
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    const ownerRow = screen.getByText('owner').closest('[data-row]');
    const plainRow = screen.getByText('plain').closest('[data-row]');
    expect(ownerRow).toBeTruthy();
    expect(plainRow).toBeTruthy();
    // The owner row has no Ban/Delete buttons; the plain row does.
    expect(ownerRow!.querySelectorAll('button')).toHaveLength(0);
    expect(plainRow!.querySelectorAll('button').length).toBeGreaterThan(0);
  });

  // ---- Phase 4: mutation wiring at the page level ----

  it('opens the Ban modal on a plain row, submits, and the chip becomes "Active ban"', async () => {
    const user = userEvent.setup();
    const plain = makeAdminUser({ nick: 'plain', sub: 'auth0|plain' });
    vi.spyOn(api, 'getAdminUsers').mockResolvedValue({
      items: [plain], page: 1, page_size: 20, total: 1, total_pages: 1,
    });
    const banSpy = vi.spyOn(api, 'banUser').mockResolvedValue({
      is_banned: true, reason: 'spamming', banned_until: '2099-01-01T00:00:00Z',
      lifted_at: null, has_prior_ban: true,
    });
    renderPage();
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());

    await user.click(screen.getByRole('button', { name: /^ban$/i }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    await user.type(screen.getByRole('textbox', { name: /reason/i }), 'spamming the range');
    await user.click(screen.getByRole('button', { name: /confirm ban/i }));

    await waitFor(() => expect(banSpy).toHaveBeenCalled());
    // The row now shows the active-ban chip + an Unban button (Ban is gone).
    await waitFor(() => expect(screen.getByText(/active ban/i)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /unban/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^ban$/i })).toBeNull();
  });

  it('unbans a banned row and the chip becomes "Banned before"', async () => {
    const user = userEvent.setup();
    const banned = makeAdminUser({
      nick: 'banned', sub: 'auth0|banned',
      ban: { is_banned: true, reason: 'x', banned_until: '2099-01-01T00:00:00Z', lifted_at: null, has_prior_ban: true },
    });
    vi.spyOn(api, 'getAdminUsers').mockResolvedValue({
      items: [banned], page: 1, page_size: 20, total: 1, total_pages: 1,
    });
    vi.spyOn(api, 'unbanUser').mockResolvedValue({
      is_banned: false, reason: 'x', banned_until: '2099-01-01T00:00:00Z', lifted_at: '2024-01-01T00:00:00Z', has_prior_ban: true,
    });
    renderPage();
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    expect(screen.getByText(/active ban/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /unban/i }));

    await waitFor(() => expect(screen.getByText(/banned before/i)).toBeInTheDocument());
    // Unban replaced by Ban again.
    expect(screen.getByRole('button', { name: /^ban$/i })).toBeInTheDocument();
  });

  it('opens the Delete modal and removes the row on confirm', async () => {
    const user = userEvent.setup();
    const plain = makeAdminUser({ nick: 'plain', sub: 'auth0|plain' });
    vi.spyOn(api, 'getAdminUsers').mockResolvedValue({
      items: [plain], page: 1, page_size: 20, total: 1, total_pages: 1,
    });
    const delSpy = vi.spyOn(api, 'deleteUser').mockResolvedValue(undefined);
    renderPage();
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());

    await user.click(screen.getByRole('button', { name: /delete/i }));
    expect(screen.getByText(/cannot do that for you/i)).toBeInTheDocument(); // the reminder note

    await user.click(screen.getByRole('button', { name: /delete permanently/i }));
    await waitFor(() => expect(delSpy).toHaveBeenCalledWith('auth0|plain'));
    // The row is gone.
    await waitFor(() => expect(screen.queryByText('plain')).toBeNull());
  });

  it('keeps total_pages from the global total after a delete (pager stays visible on a multi-page list)', async () => {
    // Regression for the total_pages-after-delete miscompute: deleting a row on
    // page 1 of a 2-page list must NOT shrink total_pages to 1 (which would hide
    // the pager and strand page 2). total_pages must follow the global total.
    const user = userEvent.setup();
    const row = makeAdminUser({ nick: 'deleteme', sub: 'auth0|deleteme' });
    vi.spyOn(api, 'getAdminUsers').mockResolvedValue({
      items: [row], page: 1, page_size: 20, total: 25, total_pages: 2,
    });
    vi.spyOn(api, 'deleteUser').mockResolvedValue(undefined);
    renderPage();
    await waitFor(() => expect(screen.getByText(/page 1 of 2/i)).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: /delete/i }));
    await user.click(screen.getByRole('button', { name: /delete permanently/i }));

    // total 25 → 24 over page_size 20 still rounds up to 2 pages: pager visible.
    await waitFor(() => expect(screen.getByText(/page 1 of 2/i)).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /next/i })).not.toBeNull();
  });
});
