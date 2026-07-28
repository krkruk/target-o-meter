// S-03: HeroStats — top-line numbers (total shots, last session, best result).
// Now consumes real aggregation data via props (S-02 read from a mocked
// fixture). Three stat cards in a row; each carries an accessible label.
import type { HeroStats as HeroStatsData } from '../api';
import styles from './HeroStats.module.css';

interface Props {
  hero: HeroStatsData | null;
}

export function HeroStats({ hero }: Props) {
  const totalShots = hero?.total_shots ?? 0;
  const lastSession = hero?.last_session_average ?? null;
  const best = hero?.best_result ?? null;
  return (
    <div className={styles.row} role="region" aria-label="Hero stats">
      <StatCard label="Total shots" value={String(totalShots)} />
      <StatCard
        label="Last session average"
        value={lastSession == null ? '—' : lastSession.toFixed(1)}
      />
      <StatCard
        label="Best result"
        value={best == null ? '—' : best.toFixed(1)}
      />
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.card}>
      <span className={styles.value}>{value}</span>
      <span className={styles.label}>{label}</span>
    </div>
  );
}
