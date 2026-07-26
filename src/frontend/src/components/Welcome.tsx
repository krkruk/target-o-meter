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
        <button className={styles.loginBtn} onClick={onLogin}>Login</button>
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
