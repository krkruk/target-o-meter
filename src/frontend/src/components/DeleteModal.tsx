// user-score-dashboard Phase 4: <DeleteModal> — hard-delete a score (confirm).
//
// Mirrors DeleteUserModal's shell: props {result, onClose, onDeleted}; overlay
// + card + Esc-to-dismiss (unless pending); Cancel = onClose (no API call);
// "Delete permanently" = deleteScore(result.result_id) then onDeleted + onClose.
// 404 (not-mine / already-gone) surfaces inline.
import { useEffect, useState } from 'react';
import { deleteScore, type ResultSummary } from '../api';
import styles from './DeleteModal.module.css';

interface DeleteModalProps {
  result: ResultSummary;
  onClose: () => void;
  onDeleted: () => void;
}

export function DeleteModal({ result, onClose, onDeleted }: DeleteModalProps) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Esc-to-dismiss (overlay-click + Cancel wired below). A pending submit is
  // left alone so Esc can't interrupt an in-flight delete.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape' && !pending) onClose();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, pending]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      await deleteScore(result.result_id);
      onDeleted();
      onClose();
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 404) setError('Score not found (it may already be gone).');
      else setError('Could not delete the score. Try again.');
    } finally {
      setPending(false);
    }
  }

  const dateLabel = result.created_at.slice(0, 10);
  return (
    <div
      className={styles.overlay}
      data-overlay
      role="dialog"
      aria-label={`Delete score from ${dateLabel}`}
      onClick={onClose}
    >
      <form
        className={styles.card}
        onClick={(e) => e.stopPropagation()}
        onSubmit={handleSubmit}
      >
        <h2 className={styles.title}>Delete score from {dateLabel}?</h2>
        <p className={styles.warning}>
          This permanently removes the score and its target image. This cannot be undone.
        </p>

        {error && <p className={styles.error} role="alert">{error}</p>}

        <div className={styles.actions}>
          <button type="button" className={styles.cancel} onClick={onClose} disabled={pending}>
            Cancel
          </button>
          <button type="submit" className={styles.danger} disabled={pending}>
            {pending ? 'Deleting…' : 'Delete permanently'}
          </button>
        </div>
      </form>
    </div>
  );
}
