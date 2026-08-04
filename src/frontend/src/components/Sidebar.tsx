// Phase 3: Sidebar — collapsible left nav. Home pinned to the top, Logout
// pinned to the bottom (flex margin-top: auto). S-04: the previously-disabled
// Admin entry is now a real <Link to="/admin">, surfaced only for owners.
import { Link } from 'react-router-dom';
import styles from './Sidebar.module.css';

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
  onLogout: () => void;
  onHome?: () => void;
  isOwner?: boolean;
}

export function Sidebar({ collapsed, onToggle, onLogout, onHome, isOwner }: SidebarProps) {
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
        <button role="menuitem" className={styles.item} onClick={onHome}>
          {collapsed ? '⌂' : 'Home'}
        </button>
        <Link role="menuitem" className={styles.item} to="/scores">
          {collapsed ? '🎯' : 'Score dashboard'}
        </Link>
        {isOwner && (
          <Link role="menuitem" className={styles.item} to="/admin">
            {collapsed ? '⚙' : 'Admin'}
          </Link>
        )}
      </div>

      <div className={styles.bottomItems}>
        <button role="menuitem" className={styles.item} onClick={onLogout}>
          {collapsed ? '⏻' : 'Logout'}
        </button>
      </div>
    </nav>
  );
}
