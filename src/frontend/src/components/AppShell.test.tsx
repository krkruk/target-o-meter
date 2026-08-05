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
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { AppShell } from './AppShell';
import type { Me } from '../api';

function makeMe(overrides: Partial<Me> = {}): Me {
  return {
    authenticated: true,
    user: { nick: 'alice', role: 'user', has_set_nick: true },
    ...overrides,
  };
}

// S-02 Phase 6.3: AppShell now mounts <Routes> and calls useNavigate(), so it
// must render inside a <Router>. The existing tests wrap their renders in
// MemoryRouter via this helper (analogous to the Phase 3.2a patch-target
// migration — when the component's context requirements change, the tests
// must follow).
function renderInRouter(ui: React.ReactElement) {
  return render(
    <MemoryRouter initialEntries={['/dashboard']}>{ui}</MemoryRouter>
  );
}

describe('AppShell', () => {
  it('renders the app brand and the nick in the top bar', () => {
    renderInRouter(<AppShell me={makeMe()} onLogout={() => {}} />);
    const banner = screen.getByRole('banner');
    expect(banner).toHaveTextContent(/target-o-meter/i);
    expect(banner).toHaveTextContent(/alice/);
  });

  it('renders Home as the first sidebar entry and Logout pinned at the bottom', () => {
    renderInRouter(<AppShell me={makeMe()} onLogout={() => {}} />);
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
    const { container } = renderInRouter(<AppShell me={makeMe()} onLogout={() => {}} />);
    const toggle = screen.getByRole('button', { name: /collapse|expand|menu|toggle/i });
    // The shell exposes its collapsed state on a data attribute so the test
    // asserts on the observable state, not a class name.
    const shell = container.querySelector('[data-shell]') as HTMLElement;
    const before = shell.getAttribute('data-collapsed');
    await userEvent.click(toggle);
    const after = shell.getAttribute('data-collapsed');
    expect(after).not.toBe(before);
  });

  // Regression: the sidebar's <nav> must carry data-collapsed so the CSS width
  // rule (.sidebar[data-collapsed="true"] → 56px) actually matches. The shell
  // signal alone isn't enough — the grey box stayed 200px when collapsed because
  // the attribute lived only on the parent .shell, never on the <nav> itself.
  // jsdom can't compute layout, so this pins the DOM signal the CSS keys off.
  it('reflects the collapsed state on the sidebar nav element', async () => {
    renderInRouter(<AppShell me={makeMe()} onLogout={() => {}} />);
    const nav = screen.getByRole('navigation', { name: /main navigation/i });
    // Expanded first.
    expect(nav.getAttribute('data-collapsed')).toBe('false');
    await userEvent.click(screen.getByRole('button', { name: /collapse sidebar/i }));
    expect(nav.getAttribute('data-collapsed')).toBe('true');
    await userEvent.click(screen.getByRole('button', { name: /expand sidebar/i }));
    expect(nav.getAttribute('data-collapsed')).toBe('false');
  });

  it('fires onLogout when the Sidebar Logout entry is activated', async () => {
    const onLogout = vi.fn();
    renderInRouter(<AppShell me={makeMe()} onLogout={onLogout} />);
    // Scope to the sidebar nav: ui-chores Phase 2 added a second Logout
    // menuitem in the TopBar, so a global role query is now ambiguous. The
    // Sidebar entry remains the target of this regression test.
    const nav = screen.getByRole('navigation', { name: /main navigation/i });
    const logout = Array.from(nav.querySelectorAll('[role="menuitem"]'))
      .find((el) => /logout/i.test(el.textContent ?? ''))!;
    await userEvent.click(logout);
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it('renders the routed Dashboard component in the main area at /dashboard', () => {
    // S-02 Phase 6: the main area is now owned by <Routes>. At /dashboard the
    // Dashboard route renders (Phase 7 fills in its content; Phase 6 ships a
    // stub with data-testid="dashboard-route"). The placeholder is gone.
    renderInRouter(<AppShell me={makeMe()} onLogout={() => {}} />);
    const main = screen.getByRole('main');
    expect(main.querySelector('[data-testid="dashboard-route"]')).not.toBeNull();
  });

  it('renders an enabled Admin link for owners (S-04: the seam became a route)', () => {
    renderInRouter(
      <AppShell
        me={makeMe({ user: { nick: 'owner', role: 'owner', has_set_nick: true } })}
        onLogout={() => {}}
      />
    );
    // The Admin entry renders as a <Link> (an <a href="/admin">) carrying
    // role="menuitem" to match the other sidebar entries. It is no longer the
    // disabled button from S-01 — it navigates to /admin.
    const admin = screen.getByRole('menuitem', { name: /admin/i });
    expect(admin).toBeInTheDocument();
    expect(admin).not.toBeDisabled();
    expect(admin.tagName).toBe('A');
    expect(admin).toHaveAttribute('href', '/admin');
  });

  it('does not render the Admin entry for plain users', () => {
    renderInRouter(<AppShell me={makeMe()} onLogout={() => {}} />);
    expect(screen.queryByText(/admin/i)).toBeNull();
  });

  // S-02 Phase 6.3/6.5: the main area is now owned by <Routes>. Assert that a
  // routed component renders inside the shell (the router mount is wired), and
  // that the Sidebar's Home button navigates to /dashboard (the unwired onHome
  // from S-01 is now router-driven).
  it('renders a routed component inside the shell main area', () => {
    render(
      <MemoryRouter initialEntries={['/dashboard']}>
        <Routes>
          <Route path="/*" element={<AppShell me={makeMe()} onLogout={() => {}} />} />
        </Routes>
      </MemoryRouter>
    );
    const main = screen.getByRole('main');
    expect(main).toBeInTheDocument();
    // The shell still renders its chrome (brand + nav) around the routed content.
    expect(screen.getByRole('banner')).toHaveTextContent(/target-o-meter/i);
    expect(screen.getByRole('navigation')).toBeInTheDocument();
  });

  it('navigates to /dashboard when the Sidebar Home button is activated', async () => {
    // A LocationProbe inside the router reads the current path after the click
    // — asserting on the routed path is more honest than a sentinel flag.
    let currentPath = '';
    function LocationProbe() {
      const loc = useLocation();
      currentPath = loc.pathname;
      return null;
    }
    render(
      <MemoryRouter initialEntries={['/capture']}>
        <LocationProbe />
        <Routes>
          <Route
            path="/*"
            element={<AppShell me={makeMe()} onLogout={() => {}} />}
          />
        </Routes>
      </MemoryRouter>
    );
    // Before clicking, we're on /capture.
    expect(currentPath).toBe('/capture');
    await userEvent.click(screen.getByRole('menuitem', { name: /home/i }));
    // After clicking Home, the router navigated to /dashboard.
    expect(currentPath).toBe('/dashboard');
  });

  // user-score-dashboard Phase 4: Home / Score dashboard / Admin are now
  // <NavLink>, so aria-current="page" marks the active entry. Pin the active
  // styling contract (the user can see which page they're on).
  it('marks the active sidebar entry with aria-current="page" on its route', () => {
    render(
      <MemoryRouter initialEntries={['/scores']}>
        <Routes>
          <Route path="/*" element={<AppShell me={makeMe()} onLogout={() => {}} />} />
        </Routes>
      </MemoryRouter>
    );
    const scoresEntry = screen.getByRole('menuitem', { name: /score dashboard/i });
    expect(scoresEntry).toHaveAttribute('aria-current', 'page');
    // The Home entry is NOT active on /scores.
    const homeEntry = screen.getByRole('menuitem', { name: /home/i });
    expect(homeEntry).not.toHaveAttribute('aria-current', 'page');
  });
});
