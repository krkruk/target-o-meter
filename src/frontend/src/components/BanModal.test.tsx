// S-04 Phase 4: BanModal contract.
//
// Duration dropdown + required free-text reason (min 5 chars). Submit calls
// banUser and surfaces 409 (cannot ban the owner) inline. Dismissable (Esc /
// overlay click / Cancel). Reuses the NickPrompt overlay/card pattern.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import * as api from '../api';
import { BanModal } from './BanModal';
import type { AdminUser } from '../api';

function makeUser(overrides: Partial<AdminUser> = {}): AdminUser {
  return {
    user_uuid: 'uuid-1', sub: 'auth0|alice', nick: 'alice', has_set_nick: true, is_owner: false,
    ban: { is_banned: false, reason: null, banned_until: null, lifted_at: null, has_prior_ban: false },
    ...overrides,
  };
}

describe('BanModal', () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it('renders a duration dropdown with the four options', () => {
    render(<BanModal user={makeUser()} onClose={() => {}} onBanned={() => {}} />);
    const select = screen.getByRole('combobox', { name: /duration/i });
    const options = Array.from(select.querySelectorAll('option')).map((o) => o.textContent);
    expect(options).toEqual(expect.arrayContaining(['1 hour', '1 day', '7 days', '30 days']));
  });

  it('disables submit until the reason reaches the 5-char minimum', async () => {
    const user = userEvent.setup();
    render(<BanModal user={makeUser()} onClose={() => {}} onBanned={() => {}} />);
    const submit = screen.getByRole('button', { name: /confirm ban|ban user/i });
    expect(submit).toBeDisabled();

    const reason = screen.getByRole('textbox', { name: /reason/i });
    await user.type(reason, 'ab'); // too short
    expect(submit).toBeDisabled();

    await user.type(reason, 'cdef'); // now 6 chars
    expect(submit).not.toBeDisabled();
  });

  it('calls banUser on submit and fires onBanned with the returned status', async () => {
    const user = userEvent.setup();
    const onBanned = vi.fn();
    const status: api.BanStatus = {
      is_banned: true, reason: 'spamming the range', banned_until: '2099-01-01T00:00:00Z',
      lifted_at: null, has_prior_ban: true,
    };
    const spy = vi.spyOn(api, 'banUser').mockResolvedValue(status);
    render(<BanModal user={makeUser()} onClose={() => {}} onBanned={onBanned} />);

    await user.type(screen.getByRole('textbox', { name: /reason/i }), 'spamming the range');
    await user.selectOptions(screen.getByRole('combobox', { name: /duration/i }), '7d');
    await user.click(screen.getByRole('button', { name: /confirm ban|ban user/i }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith('auth0|alice', { duration: '7d', reason: 'spamming the range' }));
    expect(onBanned).toHaveBeenCalledWith(status);
  });

  it('shows an inline error on 409 (cannot ban the owner)', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'banUser').mockRejectedValue(new api.HttpError(409, 'conflict'));
    render(<BanModal user={makeUser()} onClose={() => {}} onBanned={() => {}} />);

    await user.type(screen.getByRole('textbox', { name: /reason/i }), 'trying anyway');
    await user.click(screen.getByRole('button', { name: /confirm ban|ban user/i }));

    await waitFor(() => expect(screen.getByText(/cannot ban the owner|owner/i)).toBeInTheDocument());
  });

  it('is dismissable via the Cancel button', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<BanModal user={makeUser()} onClose={onClose} onBanned={() => {}} />);
    await user.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('is dismissable via the overlay click', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const { container } = render(<BanModal user={makeUser()} onClose={onClose} onBanned={() => {}} />);
    const overlay = container.querySelector('[data-overlay]') as HTMLElement;
    await user.click(overlay);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
