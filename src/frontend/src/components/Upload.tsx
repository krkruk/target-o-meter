// S-02 Phase 8: Upload route — PC file picker. Same flow as Capture minus the
// `capture` attribute. Renders CaliberDistanceStep first, then the file input.
// On file selection, calls createScoringJob and navigates to /waiting/:jobId.
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CaliberDistanceStep, type CaliberDistanceSelection } from './CaliberDistanceStep';
import { createScoringJob } from '../api';
import styles from './Upload.module.css';

export function Upload() {
  const [step, setStep] = useState<'caliber' | 'upload'>('caliber');
  const [selection, setSelection] = useState<CaliberDistanceSelection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

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
          setStep('upload');
        }}
      />
    );
  }

  return (
    <div className={styles.upload}>
      <input
        type="file"
        accept="image/*"
        aria-label="Select a photo of your target"
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
