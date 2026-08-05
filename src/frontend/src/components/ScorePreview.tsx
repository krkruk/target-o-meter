// user-score-dashboard Phase 4: <ScorePreview> — inline reveal of the target
// image + an immutable per-shot scores line.
//
// Fetches the accepted snapshot via getScore(result_id) (NOT getScoringJob —
// the accepted/corrected holes, not the raw detector output). The image comes
// through the existing same-origin proxy: /v1/scoring/jobs/{source_job}/marked-
// image (no presigned URL — storage creds stay server-side). Immutable: no
// selects; read-only. The parent toggles the reveal; this component renders
// only when open.
import { useEffect, useState } from 'react';
import { getScore, type AcceptedResult } from '../api';
import styles from './ScorePreview.module.css';

interface ScorePreviewProps {
  resultId: string;
  sourceJob: string;
}

export function ScorePreview({ resultId, sourceJob }: ScorePreviewProps) {
  const [data, setData] = useState<AcceptedResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    setError(null);
    getScore(resultId)
      .then((d) => { if (mounted) setData(d); })
      .catch(() => { if (mounted) setError('Unable to load the score preview.'); });
    return () => { mounted = false; };
  }, [resultId]);

  if (error) {
    return <p className={styles.error} role="alert">{error}</p>;
  }
  if (!data) {
    return <p className={styles.loading} role="status">Loading preview…</p>;
  }

  const scoresLine = data.holes
    .map((h) => (h.score === 10 ? 'X' : String(h.score)))
    .join(', ');

  return (
    <div className={styles.preview}>
      <img
        className={styles.image}
        src={`/v1/scoring/jobs/${sourceJob}/marked-image`}
        alt="Marked target"
      />
      <p className={styles.scores}>Scores: {scoresLine}</p>
    </div>
  );
}
