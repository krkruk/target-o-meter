// S-02 Phase 8: Results route — marked image + per-hole correction dropdowns.
//
// Fetches the ScoringJob; if result is null OR marked_image_url is null/empty,
// render an "unable to load results" fallback (the _job_to_dto fragility at
// services.py can produce a null result even on succeeded). Each hole gets a
// <select> of scores 0-10 + X; the selection updates local component state
// ONLY (no API call in S-02 — persistence is S-03).
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { getScoringJob, type ScoringJob } from '../api';
import styles from './Results.module.css';

const SCORE_OPTIONS = ['X', '10', '9', '8', '7', '6', '5', '4', '3', '2', '1', '0'];

export function Results() {
  const { jobId = '' } = useParams();
  const [job, setJob] = useState<ScoringJob | null>(null);
  const [failed, setFailed] = useState(false);
  // Local-only corrections (S-02): map of hole index -> corrected score.
  const [corrections, setCorrections] = useState<Record<number, string>>({});

  useEffect(() => {
    let mounted = true;
    getScoringJob(jobId)
      .then((j) => { if (mounted) setJob(j); })
      .catch(() => { if (mounted) setFailed(true); });
    return () => { mounted = false; };
  }, [jobId]);

  if (failed) {
    return <Fallback>Unable to load results.</Fallback>;
  }

  if (!job) {
    return (
      <div role="status" aria-label="Loading results">
        Loading…
      </div>
    );
  }

  // Null result OR missing marked image -> fallback (the _job_to_dto fragility
  // can produce a null result even on succeeded).
  if (!job.result || !job.marked_image_url) {
    return <Fallback>Unable to load results — no scored data available.</Fallback>;
  }

  return (
    <div className={styles.results}>
      <img
        className={styles.marked}
        src={job.marked_image_url}
        alt="Marked target"
        role="img"
      />
      <h3 className={styles.heading}>Holes</h3>
      <ul className={styles.holes}>
        {job.result.holes.map((hole, i) => (
          <li key={i} className={styles.hole}>
            <span className={styles.holeLabel}>
              Hole {i + 1} — detected {hole.score}
            </span>
            <label htmlFor={`correct-${i}`} className={styles.correctLabel}>
              Correct
            </label>
            <select
              id={`correct-${i}`}
              value={corrections[i] ?? String(hole.score)}
              onChange={(e) =>
                setCorrections((c) => ({ ...c, [i]: e.target.value }))
              }
            >
              {SCORE_OPTIONS.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </li>
        ))}
      </ul>
      {/* S-02: corrections are local state only. S-03 adds the save call. */}
    </div>
  );
}

function Fallback({ children }: { children: React.ReactNode }) {
  return (
    <div className={styles.fallback} role="alert">
      {children}
    </div>
  );
}

// The test asserts on role="img" name=/marked target/i — the <img> above has
// alt="Marked target" which becomes its accessible name. The aria-label is
// redundant but explicit.
