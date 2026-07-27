// S-02 Phase 8: Capture route — mobile camera capture via native
// <input type="file" accept="image/*" capture="environment"> (no library
// needed per research §5). Renders CaliberDistanceStep first, then the capture
// input. On file selection, calls createScoringJob and navigates to
// /waiting/:jobId.
import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CaliberDistanceStep, type CaliberDistanceSelection } from './CaliberDistanceStep';
import { createScoringJob } from '../api';
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
      const result = await createScoringJob(
        file,
        'air_pistol',
        selection.caliber,
        selection.distance_m,
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
