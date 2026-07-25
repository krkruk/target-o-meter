// Phase 3: TopBar — app brand (left) + logged-in nick (right).
import styles from './TopBar.module.css';

export function TopBar({ nick }: { nick: string }) {
  return (
    <header className={styles.topBar} role="banner">
      <span className={styles.brand}>Target-o-meter</span>
      <span className={styles.nick}>{nick}</span>
    </header>
  );
}
