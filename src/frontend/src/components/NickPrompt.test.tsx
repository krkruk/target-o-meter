// Phase 3: NickPrompt contract. The FR-002 UX — shown over the app shell
// when has_set_nick is false. First-login nick choice is mandatory, so the
// prompt cannot be dismissed without setting a nick.
//
// Behaviors pinned:
//   * A text input + submit are rendered.
//   * Submit is disabled while the input is empty.
//   * Submitting calls patchMe with the trimmed nick; on success the updated
//     Me is forwarded via onNickSet.
//   * A 409 (nick taken) surfaces an inline error and the prompt stays open.
//   * Whitespace-only input is treated as empty.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import * as api from '../api';
import { NickPrompt } from './NickPrompt';

describe('NickPrompt', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders a nick input and a submit control', () => {
    render(<NickPrompt onNickSet={() => {}} />);
    expect(screen.getByRole('textbox', { name: /nick/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /save|submit|set|continue/i })).toBeInTheDocument();
  });

  it('disables submit while the nick is empty', () => {
    render(<NickPrompt onNickSet={() => {}} />);
    expect(screen.getByRole('button', { name: /save|submit|set|continue/i })).toBeDisabled();
  });

  it('treats whitespace-only input as empty (submit stays disabled)', async () => {
    render(<NickPrompt onNickSet={() => {}} />);
    await userEvent.type(screen.getByRole('textbox', { name: /nick/i }), '   ');
    expect(screen.getByRole('button', { name: /save|submit|set|continue/i })).toBeDisabled();
  });

  it('enables submit once a non-empty nick is entered', async () => {
    render(<NickPrompt onNickSet={() => {}} />);
    await userEvent.type(screen.getByRole('textbox', { name: /nick/i }), 'alice');
    expect(screen.getByRole('button', { name: /save|submit|set|continue/i })).toBeEnabled();
  });

  it('calls patchMe with the trimmed nick and forwards the result on success', async () => {
    const updated = { authenticated: true, user: { nick: 'alice', role: 'user' as const, has_set_nick: true } };
    const patchMeSpy = vi.spyOn(api, 'patchMe').mockResolvedValue(updated);
    const onNickSet = vi.fn();
    render(<NickPrompt onNickSet={onNickSet} />);

    await userEvent.type(screen.getByRole('textbox', { name: /nick/i }), '  alice  ');
    await userEvent.click(screen.getByRole('button', { name: /save|submit|set|continue/i }));

    await waitFor(() => {
      expect(patchMeSpy).toHaveBeenCalledWith('alice');
      expect(onNickSet).toHaveBeenCalledWith(updated);
    });
  });

  it('surfaces an inline error and stays open on a 409 (nick taken)', async () => {
    vi.spyOn(api, 'patchMe').mockRejectedValue(new Error('PATCH /v1/me failed: 409'));
    const onNickSet = vi.fn();
    render(<NickPrompt onNickSet={onNickSet} />);

    await userEvent.type(screen.getByRole('textbox', { name: /nick/i }), 'taken');
    await userEvent.click(screen.getByRole('button', { name: /save|submit|set|continue/i }));

    await waitFor(() => {
      expect(screen.getByText(/taken|already|unavailable/i)).toBeInTheDocument();
    });
    // The prompt stays mounted — onNickSet was not called.
    expect(onNickSet).not.toHaveBeenCalled();
  });
});
