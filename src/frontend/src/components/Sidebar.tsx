// Phase 3: Sidebar — collapsible left nav. Home pinned to the top. S-04: the
// previously-disabled Admin entry is now a real <Link to="/admin">, surfaced
// only for owners.
//
// user-score-dashboard Phase 4: Home / Score dashboard / Admin are now
// <NavLink> so aria-current="page" + the .active class apply automatically
// (the user can see which page is active). Home uses <NavLink to="/dashboard"
// end> (the `end` prop avoids /dashboard matching subtrees). The Home button
// used to be <button onClick={onHome}>; that prop + AppShell's onHome wiring
// are removed since the router now owns the navigation.
//
// ui-chores (post-impl-review correction): the bottom Logout entry was removed
// — Logout now lives exclusively in the TopBar nick dropdown (Phase 2). The
// onLogout prop is gone from Sidebar; AppShell still passes it to TopBar.
import { NavLink } from 'react-router-dom';
import styles from './Sidebar.module.css';

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
  isOwner?: boolean;
}

export function Sidebar({ collapsed, onToggle, isOwner }: SidebarProps) {
  return (
    <nav
      className={styles.sidebar}
      data-collapsed={collapsed}
      role="navigation"
      aria-label="Main navigation"
    >
      <button
        className={styles.toggle}
        onClick={onToggle}
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      >
        {collapsed ? '»' : '«'}
      </button>

      <div className={styles.topItems}>
        <NavLink role="menuitem" className={styles.item} to="/dashboard" end>
          {collapsed ? '⌂' : 'Home'}
        </NavLink>
        <NavLink role="menuitem" className={styles.item} to="/scores">
          {collapsed ? '🎯' : 'Score dashboard'}
        </NavLink>
        {isOwner && (
          <NavLink role="menuitem" className={styles.item} to="/admin">
            {collapsed ? '⚙' : 'Admin'}
          </NavLink>
        )}
      </div>
    </nav>
  );
}
