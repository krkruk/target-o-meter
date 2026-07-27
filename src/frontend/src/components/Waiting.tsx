// S-02 Phase 8: Waiting route — polls getScoringJob(jobId) until a terminal
// status, then navigates to /results/:jobId (success) or shows role="alert"
// (failure). The "always resolves" guarantee comes from the BFF calling
// reap_stuck_jobs() on every poll (Phase 4.1) — no dead-end states.
//
// State machine: queued (initial) -> running (spinner) -> succeeded (navigate)
//                                                    \-> failed (role="alert").
//
// Accessibility: role="status" for the polling indicator, role="alert" for the
// error state — mirror the S-01 conventions (App.tsx loading, NickPrompt
// errors).
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { getScoringJob, type ScoringJob } from '../api';
import styles from './Waiting.module.css';

interface Props {
  /** Poll interval in ms. Defaults to 1500ms; tests pass a tiny value. */
  pollMs?: number;
}

export function Waiting({ pollMs = 1500 }: Props) {
  const { jobId = '' } = useParams();
  const navigate = useNavigate();
  const [job, setJob] = useState<ScoringJob | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    let timer: ReturnType<typeof setTimeout>;

    async function poll() {
      try {
        const current = await getScoringJob(jobId);
        if (!mounted.current) return;
        setJob(current);
        setPollError(null);
        if (current.status === 'succeeded') {
          navigate(`/results/${jobId}`);
          return; // terminal — don't schedule another poll.
        }
        if (current.status === 'failed') {
          return; // terminal — the role=alert renders from `job`.
        }
        timer = setTimeout(poll, pollMs);
      } catch (err) {
        if (!mounted.current) return;
        setPollError(err instanceof Error ? err.message : String(err));
        // A transient fetch error isn't terminal — keep polling.
        timer = setTimeout(poll, pollMs);
      }
    }

    poll();
    return () => {
      mounted.current = false;
      clearTimeout(timer);
    };
  }, [jobId, navigate, pollMs]);

  if (job?.status === 'failed') {
    return (
      <div className={styles.error} role="alert">
        <p>Scoring failed.</p>
        {job.error && <p className={styles.detail}>{job.error}</p>}
      </div>
    );
  }

  if (pollError && !job) {
    // First fetch failed before any status landed — surface as alert but keep
    // polling (the effect re-polls on transient errors).
    return (
      <div className={styles.error} role="alert">
        <p>Unable to reach the server. Retrying…</p>
      </div>
    );
  }

  const statusText =
    job?.status === 'running' ? 'Scoring your target…' : 'Queued — waiting for a worker…';

  return (
    <div className={styles.waiting} role="status" aria-live="polite">
      <span className={styles.spinner} aria-hidden="true" />
      <p>{statusText}</p>
    </div>
  );
}
