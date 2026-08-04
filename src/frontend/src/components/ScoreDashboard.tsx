// user-score-dashboard: <ScoreDashboard> — the user's score-review page at
// /scores. Read-only in Phase 3 (the action buttons render but their handlers
// are stubs); Phase 4 wires the Preview/Modify/Delete modals.
//
// Mirrors AdminUsersPage's state shape: useState for {rows, page, pageSize,
// total, totalPages, loading, error}, refetch on page/pageSize change. The
// page-size dropdown (10/20/30/50, default 20) resets page=1 on change; the
// Prev/Next pager is disabled at the bounds.
import { useCallback, useEffect, useState } from 'react';
import { getScores, type ResultSummary } from '../api';
import { ScoreList } from './ScoreList';
import styles from './ScoreDashboard.module.css';

const PAGE_SIZE_OPTIONS = [10, 20, 30, 50];

export function ScoreDashboard() {
  const [rows, setRows] = useState<ResultSummary[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [totalPages, setTotalPages] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getScores({ page, page_size: pageSize });
      setRows(data.items);
      setTotalPages(data.total_pages);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize]);

  useEffect(() => { void load(); }, [load]);

  // Phase 3 stubs — Phase 4 wires the modals (state lives per-row inside
  // <ScoreRow> then; these become success-callback refetch/remove handlers).
  const handlePreview = useCallback((_row: ResultSummary) => { /* Phase 4 */ }, []);
  const handleModified = useCallback((_row: ResultSummary) => { /* Phase 4 */ }, []);
  const handleDeleted = useCallback((_row: ResultSummary) => { /* Phase 4 */ }, []);

  function handlePageSizeChange(e: React.ChangeEvent<HTMLSelectElement>) {
    setPageSize(Number(e.target.value));
    setPage(1);
  }

  if (loading && rows.length === 0) {
    return (
      <section className={styles.page} aria-label="Score dashboard">
        <div role="status" aria-label="Loading scores">Loading scores…</div>
      </section>
    );
  }
  if (error) {
    return (
      <section className={styles.page} aria-label="Score dashboard">
        <div role="alert">Unable to load scores: {error}</div>
      </section>
    );
  }

  return (
    <section className={styles.page} aria-label="Score dashboard">
      <header className={styles.header}>
        <h1>Score dashboard</h1>
        <label className={styles.sizeControl}>
          <span className={styles.sizeLabel}>Page size</span>
          <select
            className={styles.sizeSelect}
            aria-label="Page size"
            value={pageSize}
            onChange={handlePageSizeChange}
          >
            {PAGE_SIZE_OPTIONS.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </label>
      </header>

      <ScoreList
        rows={rows}
        onPreview={handlePreview}
        onModified={handleModified}
        onDeleted={handleDeleted}
      />

      {totalPages > 1 && (
        <nav className={styles.pager} aria-label="Pagination">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            Previous
          </button>
          <span>Page {page} of {totalPages}</span>
          <button
            type="button"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
          >
            Next
          </button>
        </nav>
      )}
    </section>
  );
}
