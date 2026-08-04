// user-score-dashboard Phase 4: <ModifyModal> — edit a saved score (PATCH).
//
// Mirrors BanModal's shell: props {result, onClose, onModified}; overlay + card
// + Esc-to-dismiss (unless pending). On open, fetches the accepted snapshot
// (getScore — the corrected holes, NOT the raw detector output which would
// clobber a prior correction) and pre-fills the per-hole <select> dropdowns +
// the four param selects (copied from Results.tsx). Submit = "Modify" →
// updateScore(result.result_id, payload) → onModified() + onClose(). Cancel =
// onClose (no navigation — the user never left the dashboard).
import { useEffect, useState } from 'react';
import { CALIBERS, DISTANCES_M, WEAPON_TYPES, TARGET_TYPES } from '../taxonomy';
import {
  getScore, updateScore,
  type AcceptedResult, type ResultSummary, type AcceptedHole,
} from '../api';
import styles from './ModifyModal.module.css';

const SCORE_OPTIONS = ['X', '10', '9', '8', '7', '6', '5', '4', '3', '2', '1', '0'];

// X counts as 10 for scoring (PRD §2) — mirrors Results.tsx.
function scoreValue(opt: string): number {
  return opt === 'X' ? 10 : Number(opt);
}

interface ModifyModalProps {
  result: ResultSummary;
  onClose: () => void;
  onModified: () => void;
}

export function ModifyModal({ result, onClose, onModified }: ModifyModalProps) {
  const [snapshot, setSnapshot] = useState<AcceptedResult | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Map of hole index -> corrected score option (string from the dropdown).
  const [corrections, setCorrections] = useState<Record<number, string>>({});
  const [caliber, setCaliber] = useState<string>(CALIBERS[0]);
  const [distance, setDistance] = useState<number>(DISTANCES_M[2]);
  const [weaponType, setWeaponType] = useState<string>(WEAPON_TYPES[0]);
  const [targetType, setTargetType] = useState<string>(TARGET_TYPES[0]);
  const [pending, setPending] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Fetch the accepted snapshot on open. Pre-fill the corrections + params
  // from it once loaded.
  useEffect(() => {
    let mounted = true;
    setLoadError(null);
    getScore(result.result_id)
      .then((d) => {
        if (!mounted) return;
        setSnapshot(d);
        setCaliber(d.caliber_hint ?? CALIBERS[0]);
        setDistance(d.distance ?? DISTANCES_M[2]);
        setWeaponType(d.weapon_type ?? WEAPON_TYPES[0]);
        setTargetType(d.target_type ?? TARGET_TYPES[0]);
      })
      .catch(() => { if (mounted) setLoadError('Unable to load the score for editing.'); });
    return () => { mounted = false; };
  }, [result.result_id]);

  // Esc-to-dismiss (overlay-click + Cancel wired below). A pending submit is
  // left alone so Esc can't interrupt an in-flight modify.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape' && !pending) onClose();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, pending]);

  function buildCorrectedHoles(): AcceptedHole[] {
    return snapshot!.holes.map((hole, i) => {
      const opt = corrections[i] ?? (hole.score === 10 ? 'X' : String(hole.score));
      return {
        x: hole.x, y: hole.y,
        score: scoreValue(opt),
        confidence: hole.confidence,
        caliber: hole.caliber,
      };
    });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setSubmitError(null);
    try {
      await updateScore(result.result_id, {
        holes: buildCorrectedHoles(),
        target_type: targetType,
        caliber_hint: caliber,
        distance,
        weapon_type: weaponType,
      });
      onModified();
      onClose();
    } catch (err) {
      const status = (err as { status?: number })?.status;
      if (status === 404) setSubmitError('Score not found (it may have been deleted).');
      else setSubmitError('Could not modify the score. Try again.');
    } finally {
      setPending(false);
    }
  }

  const dateLabel = result.created_at.slice(0, 10);

  if (loadError) {
    return (
      <div
        className={styles.overlay}
        data-overlay
        role="dialog"
        aria-label={`Modify score from ${dateLabel}`}
        onClick={onClose}
      >
        <div className={styles.card} onClick={(e) => e.stopPropagation()}>
          <p className={styles.error} role="alert">{loadError}</p>
          <div className={styles.actions}>
            <button type="button" className={styles.cancel} onClick={onClose}>Close</button>
          </div>
        </div>
      </div>
    );
  }

  if (!snapshot) {
    return (
      <div
        className={styles.overlay}
        data-overlay
        role="dialog"
        aria-label={`Modify score from ${dateLabel}`}
        onClick={pending ? undefined : onClose}
      >
        <div className={styles.card} onClick={(e) => e.stopPropagation()}>
          <p className={styles.loading} role="status">Loading score…</p>
        </div>
      </div>
    );
  }

  return (
    <div
      className={styles.overlay}
      data-overlay
      role="dialog"
      aria-label={`Modify score from ${dateLabel}`}
      onClick={pending ? undefined : onClose}
    >
      <form
        className={styles.card}
        onClick={(e) => e.stopPropagation()}
        onSubmit={handleSubmit}
      >
        <h2 className={styles.title}>Modify score from {dateLabel}</h2>

        <h3 className={styles.heading}>Holes</h3>
        <ul className={styles.holes}>
          {snapshot.holes.map((hole, i) => {
            const detectedOpt = hole.score === 10 ? 'X' : String(hole.score);
            return (
              <li key={i} className={styles.hole}>
                <span className={styles.holeLabel}>
                  Hole {i + 1} — saved {detectedOpt}
                </span>
                <label htmlFor={`modify-${i}`} className={styles.correctLabel}>
                  Correct
                </label>
                <select
                  id={`modify-${i}`}
                  value={corrections[i] ?? detectedOpt}
                  onChange={(e) =>
                    setCorrections((c) => ({ ...c, [i]: e.target.value }))
                  }
                >
                  {SCORE_OPTIONS.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </li>
            );
          })}
        </ul>

        <h3 className={styles.heading}>Parameters</h3>
        <div className={styles.params}>
          <label htmlFor="m-caliber">Caliber</label>
          <select id="m-caliber" value={caliber} onChange={(e) => setCaliber(e.target.value)}>
            {CALIBERS.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>

          <label htmlFor="m-distance">Distance</label>
          <select id="m-distance" value={distance}
            onChange={(e) => setDistance(Number(e.target.value))}>
            {DISTANCES_M.map((d) => <option key={d} value={d}>{d}m</option>)}
          </select>

          <label htmlFor="m-weapon">Weapon type</label>
          <select id="m-weapon" value={weaponType}
            onChange={(e) => setWeaponType(e.target.value)}>
            {WEAPON_TYPES.map((w) => <option key={w} value={w}>{w}</option>)}
          </select>

          <label htmlFor="m-target">Target type</label>
          <select id="m-target" value={targetType}
            onChange={(e) => setTargetType(e.target.value)}>
            {TARGET_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>

        {submitError && <p className={styles.error} role="alert">{submitError}</p>}

        <div className={styles.actions}>
          <button type="button" className={styles.cancel} onClick={onClose} disabled={pending}>
            Cancel
          </button>
          <button type="submit" className={styles.submit} disabled={pending}>
            {pending ? 'Modifying…' : 'Modify'}
          </button>
        </div>
      </form>
    </div>
  );
}
