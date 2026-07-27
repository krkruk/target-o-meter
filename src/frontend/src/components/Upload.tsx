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
      // TODO(S-03): target_type is hardcoded to 'air_pistol'. PRD covers four
      // categories — air_pistol, precision_pistol, rifle, shotgun — but
      // CaliberDistanceStep doesn't collect a target-type selection yet, and
      // the BFF's Literal['air_pistol', 'precision_pistol'] only accepts the
      // first two. S-03 should add a target-type <select> covering all four
      // (and widen the BFF Literal), then thread the selection through here.
      // See S-02 impl-review F8.
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
