// Phase 3: Sidebar — collapsible left nav. Home pinned to the top, Logout
// pinned to the bottom (flex margin-top: auto). A disabled Admin entry is
// surfaced only for owners (the seam for S-04; no admin ships in S-01).
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
        {isOwner && (
          <button role="menuitem" className={styles.item} disabled aria-disabled="true">
            {collapsed ? '⚙' : 'Admin'}
          </button>
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
