// Phase 3: Welcome — the unauthenticated landing page.
//
// Top bar (app title + Login) + hero (marketing copy for ISSF shooters, the
// target SVG, a primary CTA that also logs in). Both the Login button and the
// CTA fire onLogin; App wires onLogin to the api.login() full-page nav.
import targetUrl from '../../assets/target.svg';
import styles from './Welcome.module.css';

export function Welcome({ onLogin }: { onLogin: () => void }) {
  return (
    <div className={styles.page}>
      <header className={styles.topBar} role="banner">
        <span className={styles.brand}>Target-o-meter</span>
        <div className={styles.headerActions}>
          <a
            className={styles.starBtn}
            href="https://github.com/krkruk/target-o-meter"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Star Target-o-meter on GitHub"
          >
            <svg className={styles.starIcon} viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">
              <path
                fill="currentColor"
                d="M8 .25a.75.75 0 0 1 .673.418l1.882 3.815 4.21.612a.75.75 0 0 1 .416 1.279l-3.046 2.97.719 4.192a.75.75 0 0 1-1.088.791L8 12.347l-3.766 1.98a.75.75 0 0 1-1.088-.79l.72-4.194L.818 6.374a.75.75 0 0 1 .416-1.28l4.21-.611L7.327.668A.75.75 0 0 1 8 .25z"
              />
            </svg>
            Star
          </a>
          <button className={styles.loginBtn} onClick={onLogin}>Login</button>
        </div>
      </header>
      <main className={styles.hero} role="region" aria-label="hero">
        <div className={styles.heroCopy}>
          <h1 className={styles.heroTitle}>Photograph your target. Get an objective score.</h1>
          <p className={styles.heroBody}>
            Track your progress shot by shot — an objective score from every
            target photo, with your results and trends saved over time.
          </p>
          <button className={styles.cta} onClick={onLogin}>Get started</button>
        </div>
        <img className={styles.targetImg} src={targetUrl} alt="ISSF pistol target" />
      </main>
    </div>
  );
}
