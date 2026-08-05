// S-02 Phase 8 / ui-chores Phase 5: Upload route. Renders CaliberDistanceStep
// first, then a PII/LLM-training warning callout and two custom buttons backed
// by hidden inputs — "Choose file" (always visible) and "Take a picture"
// (mobile-only via .mobileOnly + the 760px breakpoint, uses capture="environment").
// On file selection, calls createScoringJob and navigates to /waiting/:jobId.
// (/capture remains the bare-input fallback route — different surface, untouched.)
import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { BsUpload, BsCamera } from 'react-icons/bs';
import { CaliberDistanceStep, type CaliberDistanceSelection } from './CaliberDistanceStep';
import { createScoringJob } from '../api';
import { PII_WARNING } from '../pii-warning';
import styles from './Upload.module.css';

export function Upload() {
  const [step, setStep] = useState<'caliber' | 'upload'>('caliber');
  const [selection, setSelection] = useState<CaliberDistanceSelection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);

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
      <div className={styles.warning} role="note">{PII_WARNING}</div>
      <div className={styles.uploadActions}>
        <button
          type="button"
          className={styles.actionButton}
          onClick={() => fileInputRef.current?.click()}
        >
          <BsUpload aria-hidden="true" />
          Choose file
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          className={styles.hiddenInput}
          aria-label="Select a photo of your target"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) handleFile(f);
          }}
        />
        <span className={styles.mobileOnly}>
          <button
            type="button"
            className={styles.actionButton}
            onClick={() => cameraInputRef.current?.click()}
          >
            <BsCamera aria-hidden="true" />
            Take a picture
          </button>
          <input
            ref={cameraInputRef}
            type="file"
            accept="image/*"
            capture="environment"
            className={styles.hiddenInput}
            aria-label="Capture a photo of your target"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleFile(f);
            }}
          />
        </span>
      </div>
      {error && (
        <div role="alert" className={styles.error}>
          Upload failed: {error}
        </div>
      )}
    </div>
  );
}
