// Phase 3: TopBar — app brand (left) + logged-in nick (right).
// ui-chores Phase 2: the nick became a CSS-only disclosure. Hover or keyboard
// focus on the trigger reveals a small menu with a Logout action. The menu's
// visibility is stylesheet-driven (:hover / :focus-within); React state mirrors
// those same two signals so the trigger's aria-expanded reflects reality for
// assistive tech (the CSS and the ARIA stay in sync because they key off the
// same two inputs).
import { useState } from 'react';
import styles from './TopBar.module.css';

interface TopBarProps {
  nick: string;
  onLogout: () => void;
}

export function TopBar({ nick, onLogout }: TopBarProps) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  return (
    <header className={styles.topBar} role="banner">
      <span className={styles.brand}>Target-o-meter</span>
      <div
        className={styles.nickGroup}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
      >
        <button
          type="button"
          className={styles.nickTrigger}
          aria-haspopup="menu"
          aria-expanded={hovered || focused}
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
