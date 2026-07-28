// S-03: ResultsList — recent scored targets (summary view). Each row shows
// date, average score (0-10 per the PRD scoring domain), hole count, and links
// into /results/:source_job (the original ScoringJob id, so the user can
// re-view the detection). Now consumes real aggregation data via props (S-02
// read from a mocked fixture with a 0-100 score scale — the scale change is
// intentional, matching the PRD's 0-10 + X scoring domain).
import { Link } from 'react-router-dom';
import type { ResultSummary } from '../api';
import styles from './ResultsList.module.css';

interface Props {
  recent: ResultSummary[] | null;
}

export function ResultsList({ recent }: Props) {
  const rows = recent ?? [];
  return (
    <div className={styles.list} role="region" aria-label="Results">
      <h3 className={styles.heading}>Recent results</h3>
      {rows.length === 0 ? (
        <p className={styles.empty}>No results yet.</p>
      ) : (
        <ul className={styles.rows}>
          {rows.map((r) => (
            <li key={r.result_id} className={styles.row}>
              <Link to={`/results/${r.source_job}`} className={styles.link}>
                <span className={styles.date}>{r.created_at.slice(0, 10)}</span>
                <span className={styles.score}>{r.score_average.toFixed(1)}</span>
                <span className={styles.count}>{r.hole_count} holes</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
