// Phase 3: App — the single auth-seam decision point.
//
// On mount, App fetches /v1/me and renders one of three states:
//   * loading (me === null)         → spinner/placeholder
//   * unauthenticated (auth false)  → Welcome (login nav)
//   * authenticated                 → AppShell (+ NickPrompt overlay when
//                                     has_set_nick is false)
//
// These tests mock the api module to drive each branch without a network.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import * as api from './api';
import { App } from './App';

describe('App', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders a loading state before /v1/me resolves', () => {
    vi.spyOn(api, 'getMe').mockReturnValue(new Promise(() => {}));
    render(<App />);
    // The loading state is announced via aria-busy or a role; assert presence.
    expect(screen.getByRole('status', { name: /loading/i })).toBeInTheDocument();
  });

  it('renders the Welcome page when unauthenticated', async () => {
    vi.spyOn(api, 'getMe').mockResolvedValue({ authenticated: false, user: null });
    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /login/i })).toBeInTheDocument();
    });
    // And NOT the app shell.
    expect(screen.queryByText(/your dashboard will appear/i)).toBeNull();
  });

  it('renders the AppShell when authenticated, without the nick prompt if has_set_nick is true', async () => {
    vi.spyOn(api, 'getMe').mockResolvedValue({
      authenticated: true,
      user: { nick: 'alice', role: 'user', has_set_nick: true },
    });
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText(/your dashboard will appear/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole('dialog', { name: /username/i })).toBeNull();
  });

  it('renders the NickPrompt overlay when authenticated but has_set_nick is false', async () => {
    vi.spyOn(api, 'getMe').mockResolvedValue({
      authenticated: true,
      user: { nick: 'shooter-abc12345', role: 'user', has_set_nick: false },
    });
    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole('dialog', { name: /username/i })).toBeInTheDocument();
    });
  });
});
