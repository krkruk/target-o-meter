// S-02 Phase 7: HeroStats — top-line numbers (total shots, last session, best
// result). Reads from the mocked fixture (aggregation is S-03). Three stat
// cards in a row; each carries an accessible label.
import { mockHeroStats } from '../mocks/dashboard';
import styles from './HeroStats.module.css';

export function HeroStats() {
  const { totalShots, lastSessionAverage, bestResult } = mockHeroStats;
  return (
    <div className={styles.row} role="region" aria-label="Hero stats">
      <StatCard label="Total shots" value={String(totalShots)} />
      <StatCard
        label="Last session average"
        value={lastSessionAverage == null ? '—' : lastSessionAverage.toFixed(1)}
      />
      <StatCard
        label="Best result"
        value={bestResult == null ? '—' : bestResult.toFixed(1)}
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
