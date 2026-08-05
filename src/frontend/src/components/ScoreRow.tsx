// user-score-dashboard: <ScoreRow> — the single reusable row used by both the
// /scores dashboard and the home "Recent results" list.
//
// Phase 4: the row OWNS its action state (mirroring AdminUsersPage's UserRow):
//   - Preview toggles an inline <ScorePreview> reveal under the row.
//   - Modify/Delete open the modals via a per-row useState<'modify'|'delete'|
//     null> (no row field in state — the row comes from the closure).
//   - onModified / onDeleted are SUCCESS callbacks that bubble to the parent
//     (ScoreDashboard refetches; Dashboard/home removes the row).
//
// ui-chores Phase 3 (post-impl-review correction): the three action buttons
// are ICON-ONLY. The visible text was dropped (icons replace it, per the
// change's actual intent); each button keeps its descriptive aria-label so
// screen readers and tooltip-hover still announce "Preview/Modify/Delete score
// from <date>". Preview's target.svg <img> keeps alt="" (decorative — the
// button's accessible name comes from aria-label, same as Modify/Delete).
import { useState } from 'react';
import { BsPencil, BsTrash3 } from 'react-icons/bs';
import type { ResultSummary } from '../api';
import targetIcon from '../../assets/target.svg';
import { ScorePreview } from './ScorePreview';
import { ModifyModal } from './ModifyModal';
import { DeleteModal } from './DeleteModal';
import styles from './ScoreRow.module.css';

interface ScoreRowProps {
  row: ResultSummary;
  onModified: (row: ResultSummary) => void;
  onDeleted: (row: ResultSummary) => void;
}

export function ScoreRow({ row, onModified, onDeleted }: ScoreRowProps) {
  const [showPreview, setShowPreview] = useState(false);
  const [modal, setModal] = useState<'modify' | 'delete' | null>(null);
  const dateLabel = row.created_at.slice(0, 10);

  return (
    <li className={styles.row}>
      <div className={styles.inner}>
        <div className={styles.left}>
          <span className={styles.date}>{dateLabel}</span>
          <span className={styles.score}>{row.score_average.toFixed(1)}</span>
        </div>
        <div className={styles.actions}>
          <button
            type="button"
            className={styles.previewBtn}
            aria-expanded={showPreview}
            aria-label={`Preview score from ${dateLabel}`}
            onClick={() => setShowPreview((v) => !v)}
          >
            <img src={targetIcon} alt="" className={`${styles.icon} ${styles.targetIcon}`} />
          </button>
          <button
            type="button"
            className={styles.actionBtn}
            aria-label={`Modify score from ${dateLabel}`}
            onClick={() => setModal('modify')}
          >
            <BsPencil className={styles.icon} aria-hidden="true" />
          </button>
          <button
            type="button"
            className={`${styles.actionBtn} ${styles.dangerBtn}`}
            aria-label={`Delete score from ${dateLabel}`}
            onClick={() => setModal('delete')}
          >
            <BsTrash3 className={styles.icon} aria-hidden="true" />
          </button>
        </div>
      </div>

      {showPreview && (
        <ScorePreview resultId={row.result_id} sourceJob={row.source_job} />
      )}

      {modal === 'modify' && (
        <ModifyModal
          result={row}
          onClose={() => setModal(null)}
          onModified={() => { onModified(row); }}
        />
      )}
      {modal === 'delete' && (
        <DeleteModal
          result={row}
          onClose={() => setModal(null)}
          onDeleted={() => { onDeleted(row); }}
        />
      )}
    </li>
  );
}
