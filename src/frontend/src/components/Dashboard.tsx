// S-02 Phase 7: Dashboard — the single-screen CSS Grid dashboard.
//
// Four regions (hero stats, add-photos, results, daily-average chart) in a
// viewport-locked grid on laptop; collapses to a scrollable single column on
// <=760px viewports (mobile cannot honor "no scroll" — the brief's no-scroll
// is a laptop constraint, mobile falls back gracefully per the project's 760px
// convention).
//
// The add-photos button branches: on viewports <=760px it routes to /capture
// (mobile camera capture), else /upload (PC file picker) — via a
// matchMedia('(max-width: 760px)') check.
//
// All data is mocked (src/mocks/dashboard); S-03 swaps the fixtures for real
// aggregation API calls. Accessibility: each region carries role="region" +
// aria-label; the chart wrapper is role="img" (handled in DailyAverageChart).
import { useNavigate } from 'react-router-dom';
import { HeroStats } from './HeroStats';
import { ResultsList } from './ResultsList';
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

  function handleAddPhotos() {
    navigate(isMobile ? '/capture' : '/upload');
  }

  return (
    <div className={styles.dashboard} data-testid="dashboard-route">
      <section className={styles.hero}>
        <HeroStats />
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

      <section className={styles.results}>
        <ResultsList />
      </section>

      <section className={styles.chart} aria-label="Daily average">
        <DailyAverageChart />
      </section>
    </div>
  );
}
