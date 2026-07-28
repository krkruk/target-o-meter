// S-02 Phase 8 + S-03: Results route — marked image + per-hole correction
// dropdowns + the accept/reject form.
//
// Fetches the ScoringJob; if result is null OR marked_image_url is null/empty,
// render an "unable to load results" fallback. Each hole gets a <select> of
// scores 0-10 + X; the selection updates local state. S-03 adds:
//   - a "Confirm parameters" form (caliber, distance, weapon_type, target_type)
//     pre-filled from the job, editable pre-accept.
//   - an Accept button → POST /v1/scoring/results with the corrected holes +
//     params → navigate to /dashboard.
//   - a Reject button → navigate to /dashboard (no POST; FR-011).
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { CALIBERS, DISTANCES_M, WEAPON_TYPES, TARGET_TYPES } from '../taxonomy';
import {
  acceptResult, getScoringJob,
  type ScoringJob, type AcceptedHole,
} from '../api';
import styles from './Results.module.css';

const SCORE_OPTIONS = ['X', '10', '9', '8', '7', '6', '5', '4', '3', '2', '1', '0'];

// X counts as 10 for scoring (PRD §2).
function scoreValue(opt: string): number {
  return opt === 'X' ? 10 : Number(opt);
}

export function Results() {
  const { jobId = '' } = useParams();
  const navigate = useNavigate();
  const [job, setJob] = useState<ScoringJob | null>(null);
  const [failed, setFailed] = useState(false);
  // Map of hole index -> corrected score option (string from the dropdown).
  const [corrections, setCorrections] = useState<Record<number, string>>({});
  // Confirm-params form state (pre-filled from the job once loaded).
  const [caliber, setCaliber] = useState<string>(CALIBERS[0]);
  const [distance, setDistance] = useState<number>(DISTANCES_M[2]);
  const [weaponType, setWeaponType] = useState<string>(WEAPON_TYPES[0]);
  const [targetType, setTargetType] = useState<string>(TARGET_TYPES[0]);
  const [submitting, setSubmitting] = useState(false);
  const [acceptError, setAcceptError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    getScoringJob(jobId)
      .then((j) => {
        if (!mounted) return;
        setJob(j);
        // Pre-fill the params form from the wizard's selections.
        setCaliber(j.caliber_hint ?? CALIBERS[0]);
        setDistance(j.distance ?? DISTANCES_M[2]);
        setWeaponType(j.weapon_type ?? WEAPON_TYPES[0]);
        setTargetType(j.target_type ?? TARGET_TYPES[0]);
      })
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

  // Null result OR missing marked image -> fallback.
  if (!job.result || !job.marked_image_url) {
    return <Fallback>Unable to load results — no scored data available.</Fallback>;
  }

  function buildCorrectedHoles(): AcceptedHole[] {
    return job!.result!.holes.map((hole, i) => {
      const opt = corrections[i] ?? String(hole.score);
      return {
        x: hole.x, y: hole.y,
        score: scoreValue(opt),
        confidence: hole.confidence,
        caliber: hole.caliber,
      };
    });
  }

  async function handleAccept() {
    setSubmitting(true);
    setAcceptError(null);
    try {
      await acceptResult(jobId, {
        target_type: targetType,
        caliber_hint: caliber,
        distance,
        weapon_type: weaponType,
        holes: buildCorrectedHoles(),
      });
      navigate('/dashboard');
    } catch (err) {
      setSubmitting(false);
      setAcceptError(err instanceof Error ? err.message : String(err));
    }
  }

  function handleReject() {
    // FR-011: reject is the absence of a POST — just navigate away.
    navigate('/dashboard');
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

      <h3 className={styles.heading}>Confirm parameters</h3>
      <div className={styles.params}>
        <label htmlFor="p-caliber">Caliber</label>
        <select id="p-caliber" value={caliber} onChange={(e) => setCaliber(e.target.value)}>
          {CALIBERS.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>

        <label htmlFor="p-distance">Distance</label>
        <select id="p-distance" value={distance}
          onChange={(e) => setDistance(Number(e.target.value))}>
          {DISTANCES_M.map((d) => <option key={d} value={d}>{d}m</option>)}
        </select>

        <label htmlFor="p-weapon">Weapon type</label>
        <select id="p-weapon" value={weaponType}
          onChange={(e) => setWeaponType(e.target.value)}>
          {WEAPON_TYPES.map((w) => <option key={w} value={w}>{w}</option>)}
        </select>

        <label htmlFor="p-target">Target type</label>
        <select id="p-target" value={targetType}
          onChange={(e) => setTargetType(e.target.value)}>
          {TARGET_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
      </div>

      {acceptError && (
        <div role="alert" className={styles.error}>
          Accept failed: {acceptError}
        </div>
      )}

      <div className={styles.actions}>
        <button
          type="button"
          className={styles.accept}
          aria-label="Accept result"
          disabled={submitting}
          onClick={handleAccept}
        >
          Accept
        </button>
        <button
          type="button"
          className={styles.reject}
          aria-label="Reject result"
          onClick={handleReject}
        >
          Reject
        </button>
      </div>
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
