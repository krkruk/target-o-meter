// S-02 Phase 7: ResultsList — recent scored targets (summary view). Each row
// shows date, score, target count, and links into /results/:jobId. Per-hole
// correction dropdowns live in /results/:jobId (Phase 8), NOT in this list.
// Reads from the mocked fixture (aggregation is S-03).
import { Link } from 'react-router-dom';
import { mockResults } from '../mocks/dashboard';
import styles from './ResultsList.module.css';

export function ResultsList() {
  return (
    <div className={styles.list} role="region" aria-label="Results">
      <h3 className={styles.heading}>Recent results</h3>
      <ul className={styles.rows}>
        {mockResults.map((r) => (
          <li key={r.jobId} className={styles.row}>
            <Link to={`/results/${r.jobId}`} className={styles.link}>
              <span className={styles.date}>{r.date}</span>
              <span className={styles.score}>{r.score}</span>
              <span className={styles.count}>{r.targetCount} targets</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
