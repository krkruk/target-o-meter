// user-score-dashboard: <ScoreList> — rows grouped under day headers; shared
// by the /scores dashboard and the home "Recent results".
//
// The API returns rows most-recent-first; this component buckets them client-
// side by created_at.slice(0,10) (YYYY-MM-DD) and renders each group as a
// <section> with a day header + a <ul> of <ScoreRow>. No collapse. Empty
// state: a <p>No scores yet.</p>.
import type { ResultSummary } from '../api';
import { ScoreRow } from './ScoreRow';
import styles from './ScoreList.module.css';

interface ScoreListProps {
  rows: ResultSummary[];
  onPreview: (row: ResultSummary) => void;
  onModified: (row: ResultSummary) => void;
  onDeleted: (row: ResultSummary) => void;
}

interface DayGroup {
  date: string;
  rows: ResultSummary[];
}

function groupByDay(rows: ResultSummary[]): DayGroup[] {
  const groups: DayGroup[] = [];
  let current: DayGroup | null = null;
  for (const r of rows) {
    const day = r.created_at.slice(0, 10);
    if (!current || current.date !== day) {
      current = { date: day, rows: [] };
      groups.push(current);
    }
    current.rows.push(r);
  }
  return groups;
}

export function ScoreList({ rows, onPreview, onModified, onDeleted }: ScoreListProps) {
  if (rows.length === 0) {
    return <p className={styles.empty}>No scores yet.</p>;
  }

  const groups = groupByDay(rows);
  return (
    <div className={styles.list}>
      {groups.map((g) => (
        <section key={g.date} className={styles.group}>
          <h3 className={styles.dayHeader}>{g.date}</h3>
          <ul className={styles.rows}>
            {g.rows.map((r) => (
              <ScoreRow
                key={r.result_id}
                row={r}
                onPreview={onPreview}
                onModified={onModified}
                onDeleted={onDeleted}
              />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
