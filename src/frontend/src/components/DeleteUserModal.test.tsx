// S-04 Phase 4: DeleteUserModal contract.
//
// Confirmation with the Auth0 reminder note. Hard-delete is irreversible, so
// the reminder ("Also delete this person in Auth0 → Users tab; Target-o-meter
// cannot do that for you") is mandatory. Submit calls deleteUser; 409 shows
// "Cannot delete the owner". Dismissable.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import * as api from '../api';
import { DeleteUserModal } from './DeleteUserModal';
import type { AdminUser } from '../api';

function makeUser(overrides: Partial<AdminUser> = {}): AdminUser {
  return {
    user_uuid: 'uuid-1', sub: 'auth0|alice', nick: 'alice', has_set_nick: true, is_owner: false,
    ban: { is_banned: false, reason: null, banned_until: null, lifted_at: null, has_prior_ban: false },
    ...overrides,
  };
}

describe('DeleteUserModal', () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it('shows the Auth0 reminder note', () => {
    render(<DeleteUserModal user={makeUser()} onClose={() => {}} onDeleted={() => {}} />);
    expect(screen.getByText(/auth0/i)).toBeInTheDocument();
    expect(screen.getByText(/cannot do that for you/i)).toBeInTheDocument();
  });

  it('shows the nick in the confirmation prompt', () => {
    render(<DeleteUserModal user={makeUser({ nick: 'carol' })} onClose={() => {}} onDeleted={() => {}} />);
    expect(screen.getByRole('heading', { name: /carol/i })).toBeInTheDocument();
  });

  it('calls deleteUser on confirm and fires onDeleted', async () => {
    const user = userEvent.setup();
    const onDeleted = vi.fn();
    const spy = vi.spyOn(api, 'deleteUser').mockResolvedValue(undefined);
    render(<DeleteUserModal user={makeUser()} onClose={() => {}} onDeleted={onDeleted} />);

    await user.click(screen.getByRole('button', { name: /delete permanently/i }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith('auth0|alice'));
    expect(onDeleted).toHaveBeenCalledTimes(1);
  });

  it('shows an inline error on 409 (cannot delete the owner)', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'deleteUser').mockRejectedValue(new api.HttpError(409, 'conflict'));
    render(<DeleteUserModal user={makeUser()} onClose={() => {}} onDeleted={() => {}} />);

    await user.click(screen.getByRole('button', { name: /delete permanently/i }));
    await waitFor(() => expect(screen.getByText(/cannot delete the owner|owner/i)).toBeInTheDocument());
  });

  it('is dismissable via Cancel', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<DeleteUserModal user={makeUser()} onClose={onClose} onDeleted={() => {}} />);
    await user.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
