// Phase 3: TopBar — app brand (left) + logged-in nick (right).
// ui-chores Phase 2: the nick became a CSS-only disclosure. Hover or keyboard
// focus on the trigger reveals a small menu with a Logout action — pure CSS
// (:hover / :focus-within), no JS state, so the trigger's aria-expanded stays
// "false" (the menu visibility is stylesheet-driven, not toggled from React).
import styles from './TopBar.module.css';

interface TopBarProps {
  nick: string;
  onLogout: () => void;
}

export function TopBar({ nick, onLogout }: TopBarProps) {
  return (
    <header className={styles.topBar} role="banner">
      <span className={styles.brand}>Target-o-meter</span>
      <div className={styles.nickGroup}>
        <button
          type="button"
          className={styles.nickTrigger}
          aria-haspopup="menu"
          aria-expanded="false"
        >
          {nick}
        </button>
        <div role="menu" className={styles.menu}>
          <button
            type="button"
            role="menuitem"
            className={styles.menuItem}
            onClick={onLogout}
          >
            Logout
          </button>
        </div>
      </div>
    </header>
  );
}
