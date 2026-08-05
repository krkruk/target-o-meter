// ui-chores Phase 2: TopBar disclosure contract. The nick becomes a CSS-only
// disclosure (hover/focus-within) revealing a Logout menu item. jsdom can't
// honor :hover/:focus-within visibility, so the tests pin the DOM contract
// the CSS keys off (aria-haspopup, role="menu", the Logout menuitem) and the
// click-handler wiring — the same things a screen reader / keyboard user
// would observe.
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TopBar } from './TopBar';

describe('TopBar', () => {
  it('renders the brand and the nick', () => {
    render(<TopBar nick="alice" onLogout={() => {}} />);
    const banner = screen.getByRole('banner');
    expect(banner).toHaveTextContent(/target-o-meter/i);
    expect(banner).toHaveTextContent(/alice/);
  });

  it('exposes the nick as a menu trigger (aria-haspopup) with a role="menu" container', () => {
    render(<TopBar nick="alice" onLogout={() => {}} />);
    const trigger = screen.getByRole('button', { name: 'alice' });
    expect(trigger).toHaveAttribute('aria-haspopup', 'menu');
    expect(trigger.tagName).toBe('BUTTON');
    const menu = screen.getByRole('menu');
    expect(menu).toBeInTheDocument();
  });

  it('toggles aria-expanded to reflect menu visibility on keyboard focus', async () => {
    render(<TopBar nick="alice" onLogout={() => {}} />);
    const trigger = screen.getByRole('button', { name: 'alice' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    await userEvent.tab(); // moves focus into the trigger
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    await userEvent.tab({ shift: true }); // moves focus back out
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
  });

  it('renders the Logout action as a menuitem inside the menu', () => {
    render(<TopBar nick="alice" onLogout={() => {}} />);
    const logout = screen.getByRole('menuitem', { name: /logout/i });
    expect(logout.tagName).toBe('BUTTON');
  });

  it('calls onLogout when the Logout menuitem is clicked', async () => {
    const onLogout = vi.fn();
    render(<TopBar nick="alice" onLogout={onLogout} />);
    await userEvent.click(screen.getByRole('menuitem', { name: /logout/i }));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it('keeps the trigger keyboard-focusable (it is a native button)', () => {
    render(<TopBar nick="alice" onLogout={() => {}} />);
    const trigger = screen.getByRole('button', { name: 'alice' });
    expect(trigger.tagName).toBe('BUTTON');
    expect(trigger).not.toHaveAttribute('tabindex', '-1');
  });
});
