// Phase 3: AppShell contract. The authenticated layout — top bar (title +
// nick) + collapsible left sidebar (Home top, Logout bottom) + dashboard
// placeholder main area. Pins the structural invariants the App relies on:
//   * TopBar shows the app brand + the logged-in nick.
//   * Sidebar collapses/expands via a toggle.
//   * Home is the first nav item; Logout is pinned to the bottom.
//   * Logout fires onLogout (App wires it to postLogout + reload).
//   * A dashboard placeholder is rendered (S-02/S-03 fill it in).
//   * role === 'owner' surfaces a disabled Admin entry (seam for S-04).
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AppShell } from './AppShell';
import type { Me } from '../api';

function makeMe(overrides: Partial<Me> = {}): Me {
  return {
    authenticated: true,
    user: { nick: 'alice', role: 'user', has_set_nick: true },
    ...overrides,
  };
}

describe('AppShell', () => {
  it('renders the app brand and the nick in the top bar', () => {
    render(<AppShell me={makeMe()} onLogout={() => {}} />);
    const banner = screen.getByRole('banner');
    expect(banner).toHaveTextContent(/target-o-meter/i);
    expect(banner).toHaveTextContent(/alice/);
  });

  it('renders Home as the first sidebar entry and Logout pinned at the bottom', () => {
    render(<AppShell me={makeMe()} onLogout={() => {}} />);
    const nav = screen.getByRole('navigation');
    // Nav items carry role="menuitem"; the collapse toggle is a plain button
    // and is excluded so the order assertion targets the menu, not the chrome.
    const items = nav.querySelectorAll('[role="menuitem"]');
    const labels = Array.from(items).map((el) => el.textContent?.trim() || '');
    // Home is first, Logout is last — the order is the contract.
    expect(labels[0]).toMatch(/home/i);
    expect(labels[labels.length - 1]).toMatch(/logout/i);
  });

  it('collapses and expands the sidebar via the toggle', async () => {
    const { container } = render(<AppShell me={makeMe()} onLogout={() => {}} />);
    const toggle = screen.getByRole('button', { name: /collapse|expand|menu|toggle/i });
    // The shell exposes its collapsed state on a data attribute so the test
    // asserts on the observable state, not a class name.
    const shell = container.querySelector('[data-shell]') as HTMLElement;
    const before = shell.getAttribute('data-collapsed');
    await userEvent.click(toggle);
    const after = shell.getAttribute('data-collapsed');
    expect(after).not.toBe(before);
  });

  it('fires onLogout when the Logout entry is activated', async () => {
    const onLogout = vi.fn();
    render(<AppShell me={makeMe()} onLogout={onLogout} />);
    await userEvent.click(screen.getByRole('menuitem', { name: /logout/i }));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it('renders a dashboard placeholder in the main area', () => {
    render(<AppShell me={makeMe()} onLogout={() => {}} />);
    const main = screen.getByRole('main');
    // S-01 ships a placeholder; S-02/S-03 replace it with real content.
    expect(main.textContent?.toLowerCase()).toMatch(/dashboard|placeholder|will appear|coming/);
  });

  it('renders a disabled Admin entry for owners (seam for S-04)', () => {
    render(
      <AppShell
        me={makeMe({ user: { nick: 'owner', role: 'owner', has_set_nick: true } })}
        onLogout={() => {}}
      />
    );
    const admin = screen.queryByRole('menuitem', { name: /admin/i })
      ?? screen.queryByRole('button', { name: /admin/i });
    expect(admin).not.toBeNull();
    expect(admin).toBeDisabled();
  });

  it('does not render the Admin entry for plain users', () => {
    render(<AppShell me={makeMe()} onLogout={() => {}} />);
    expect(screen.queryByText(/admin/i)).toBeNull();
  });
});
