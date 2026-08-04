// user-score-dashboard: <ScoreRow> — the single reusable row used by both the
// /scores dashboard and the home "Recent results" list.
//
// Left: date (YYYY-MM-DD) + bolded score_average. Right: Preview / Modify /
// Delete buttons (type="button"). The on* handlers are stubs at the page level
// in Phase 3 (no modals yet); Phase 4 moves the modal state INTO <ScoreRow>
// (mirroring AdminUsersPage's UserRow), so the mutate props become success
// callbacks onModified/onDeleted — those names are used here from the start to
// avoid a Phase-4 rename. The Phase-3 Preview handler is passed through as
// onPreview; the row owns its own preview reveal in Phase 4.
//
// NOTE: Phase 4 will also render <ModifyModal>/<DeleteModal> from inside this
// row. To keep Phase 3's prop surface stable, onModified/onDeleted are declared
// here even though they're no-ops until the modals land.
import type { ResultSummary } from '../api';
import targetIcon from '../../assets/target.svg';
import styles from './ScoreRow.module.css';

interface ScoreRowProps {
  row: ResultSummary;
  onPreview: (row: ResultSummary) => void;
  onModified: (row: ResultSummary) => void;
  onDeleted: (row: ResultSummary) => void;
}

export function ScoreRow({ row, onPreview, onModified, onDeleted }: ScoreRowProps) {
  return (
    <li className={styles.row}>
      <div className={styles.left}>
        <span className={styles.date}>{row.created_at.slice(0, 10)}</span>
        <span className={styles.score}>{row.score_average.toFixed(1)}</span>
      </div>
      <div className={styles.actions}>
        <button
          type="button"
          className={styles.previewBtn}
          aria-label={`Preview score from ${row.created_at.slice(0, 10)}`}
          onClick={() => onPreview(row)}
        >
          <img src={targetIcon} alt="" className={styles.icon} />
          Preview
        </button>
        <button
          type="button"
          className={styles.actionBtn}
          aria-label={`Modify score from ${row.created_at.slice(0, 10)}`}
          onClick={() => onModified(row)}
        >
          Modify
        </button>
        <button
          type="button"
          className={`${styles.actionBtn} ${styles.dangerBtn}`}
          aria-label={`Delete score from ${row.created_at.slice(0, 10)}`}
          onClick={() => onDeleted(row)}
        >
          Delete
        </button>
      </div>
    </li>
  );
}
