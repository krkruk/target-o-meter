// S-03: Dashboard — the single-screen CSS Grid dashboard.
//
// Four regions (hero stats, add-photos, results, daily-average chart) in a
// viewport-locked grid on laptop; collapses to a scrollable single column on
// <=760px viewports. S-03 swapped the S-02 mocked fixtures for real
// aggregation API calls (getAggregations on mount — refetch-on-navigate, no
// Redux/Oval). Accessibility: each region carries role="region" + aria-label;
// the chart wrapper is role="img" (handled in DailyAverageChart). Loading →
// role="status"; error → role="alert".
//
// user-score-dashboard: the "Recent results" region now reuses the shared
// <ScoreList> fed by getScores({page:1, page_size:20}) (was getAggregations().
// recent capped at 10). Hero stats + chart still use getAggregations().
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getAggregations, getScores, type Aggregations, type ResultSummary } from '../api';
import { HeroStats } from './HeroStats';
import { ScoreList } from './ScoreList';
import { DailyAverageChart } from './DailyAverageChart';
import styles from './Dashboard.module.css';

function useIsMobile(): boolean {
  // jsdom doesn't implement matchMedia; default to desktop (false) when absent.
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  return window.matchMedia('(max-width: 760px)').matches;
}

export function Dashboard() {
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const [aggregations, setAggregations] = useState<Aggregations | null>(null);
  const [recentRows, setRecentRows] = useState<ResultSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    getAggregations()
      .then((agg) => { if (mounted) setAggregations(agg); })
      .catch((err) => { if (mounted) setError(err instanceof Error ? err.message : String(err)); });
    getScores({ page: 1, page_size: 20 })
      .then((data) => { if (mounted) setRecentRows(data.items); })
      .catch(() => { /* hero-stats error path already covers load failure */ });
    return () => { mounted = false; };
  }, []);

  // Phase 4: the row owns the modal state; these are SUCCESS callbacks.
  // onModified / onDeleted → refetch BOTH the 20-row "Recent results" AND the
  //   aggregations (hero best_result/last_session_average + daily-averages
  //   chart), because a modify or delete can change the hero stats and the
  //   chart's daily average. Without the aggregations refetch the hero and
  //   chart stay stale until a full page reload (impl-review F2).
  const refetchRecent = useCallback(() => {
    let mounted = true;
    getScores({ page: 1, page_size: 20 })
      .then((data) => { if (mounted) setRecentRows(data.items); })
      .catch(() => { /* hero-stats error path already covers load failure */ });
    return () => { mounted = false; };
  }, []);
  const refetchAggregations = useCallback(() => {
    let mounted = true;
    getAggregations()
      .then((agg) => { if (mounted) setAggregations(agg); })
      .catch(() => { /* hero-stats error path already covers load failure */ });
    return () => { mounted = false; };
  }, []);
  const handleModified = useCallback(() => {
    refetchRecent();
    refetchAggregations();
  }, [refetchRecent, refetchAggregations]);
  const handleDeleted = useCallback((row: ResultSummary) => {
    setRecentRows((prev) => prev.filter((r) => r.result_id !== row.result_id));
    refetchAggregations();
  }, [refetchAggregations]);

  function handleAddPhotos() {
    navigate(isMobile ? '/capture' : '/upload');
  }

  if (error) {
    return (
      <div className={styles.dashboard} data-testid="dashboard-route">
        <div role="alert">Unable to load dashboard: {error}</div>
      </div>
    );
  }

  return (
    <div className={styles.dashboard} data-testid="dashboard-route">
      <section className={styles.hero}>
        {aggregations ? (
          <HeroStats hero={aggregations.hero} />
        ) : (
          <div role="status" aria-label="Loading hero stats">Loading…</div>
        )}
      </section>

      <section className={styles.addPhotos} aria-label="Add photos">
        <button
          type="button"
          className={styles.addPhotosBtn}
          onClick={handleAddPhotos}
          aria-label={isMobile ? 'Add photos via camera' : 'Add photos via upload'}
        >
          Add photos
        </button>
      </section>

      <section className={styles.results} aria-label="Results">
        <ScoreList
          rows={recentRows}
          onModified={handleModified}
          onDeleted={handleDeleted}
        />
      </section>

      <section className={styles.chart} aria-label="Daily average">
        <DailyAverageChart daily={aggregations?.daily_averages ?? null} />
      </section>
    </div>
  );
}
