// S-02 Phase 8: CaliberDistanceStep — shared wizard step before media
// acquisition. Two accessible <select>s (caliber + distance). On "Next,"
// passes the selections to the parent via onNext.
import { useState } from 'react';
import { CALIBERS, DISTANCES_M } from '../taxonomy';
import styles from './CaliberDistanceStep.module.css';

export interface CaliberDistanceSelection {
  caliber: string;
  distance_m: number;
}

interface Props {
  onNext: (selection: CaliberDistanceSelection) => void;
}

export function CaliberDistanceStep({ onNext }: Props) {
  const [caliber, setCaliber] = useState<string>(CALIBERS[0]);
  const [distance, setDistance] = useState<number>(DISTANCES_M[2]); // 25m default

  return (
    <div className={styles.step}>
      <div className={styles.field}>
        <label htmlFor="caliber">Caliber</label>
        <select
          id="caliber"
          value={caliber}
          onChange={(e) => setCaliber(e.target.value)}
        >
          {CALIBERS.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
      </div>

      <div className={styles.field}>
        <label htmlFor="distance">Distance</label>
        <select
          id="distance"
          value={distance}
          onChange={(e) => setDistance(Number(e.target.value))}
        >
          {DISTANCES_M.map((d) => (
            <option key={d} value={d}>{d}m</option>
          ))}
        </select>
      </div>

      <button
        type="button"
        className={styles.next}
        onClick={() => onNext({ caliber, distance_m: distance })}
      >
        Next
      </button>
    </div>
  );
}
