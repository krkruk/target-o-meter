// S-02 Phase 8: Capture route — mobile camera capture via native
// <input type="file" accept="image/*" capture="environment"> (no library
// needed per research §5). Renders CaliberDistanceStep first, then the capture
// input. On file selection, calls createScoringJob and navigates to
// /waiting/:jobId.
//
// fix/add-missing-warning-and-gallery-button-in-mobile: this route is no longer
// the mobile entry point (Dashboard routes every platform to /upload), but it
// is retained as a direct-URL fallback. The PII warning callout is rendered
// here too so any user who lands on /capture directly still sees the warning
// regardless of platform.
import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CaliberDistanceStep, type CaliberDistanceSelection } from './CaliberDistanceStep';
import { createScoringJob } from '../api';
import { PII_WARNING } from '../pii-warning';
import styles from './Capture.module.css';

export function Capture() {
  const [step, setStep] = useState<'caliber' | 'capture'>('caliber');
  const [selection, setSelection] = useState<CaliberDistanceSelection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleFile(file: File) {
    if (!selection) return;
    try {
      // S-03: target_type + weapon_type now come from the wizard (FR-009),
      // threaded through to createScoringJob.
      const result = await createScoringJob(
        file,
        selection.target_type,
        selection.caliber,
        selection.distance_m,
        selection.weapon_type,
      );
      navigate(`/waiting/${result.job_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (step === 'caliber') {
    return (
      <CaliberDistanceStep
        onNext={(sel) => {
          setSelection(sel);
          setStep('capture');
        }}
      />
    );
  }

  return (
    <div className={styles.capture}>
      <div className={styles.warning} role="note">{PII_WARNING}</div>
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        capture="environment"
        aria-label="Capture a photo of your target"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) handleFile(f);
        }}
      />
      {error && (
        <div role="alert" className={styles.error}>
          Upload failed: {error}
        </div>
      )}
    </div>
  );
}
