// S-02 Phase 8 + S-03 FR-009: CaliberDistanceStep — shared wizard step before
// media acquisition. Four accessible <select>s (caliber, distance, weapon_type,
// target_type). On "Next," passes the selections to the parent via onNext.
import { useState } from 'react';
import { CALIBERS, DISTANCES_M, WEAPON_TYPES, TARGET_TYPES } from '../taxonomy';
import styles from './CaliberDistanceStep.module.css';

export interface CaliberDistanceSelection {
  caliber: string;
  distance_m: number;
  weapon_type: string;
  target_type: string;
}

interface Props {
  onNext: (selection: CaliberDistanceSelection) => void;
}

export function CaliberDistanceStep({ onNext }: Props) {
  const [caliber, setCaliber] = useState<string>(CALIBERS[0]);
  const [distance, setDistance] = useState<number>(DISTANCES_M[2]); // 25m default
  const [weaponType, setWeaponType] = useState<string>(WEAPON_TYPES[0]);
  // S-03: target_type is now user-selectable (S-02 hardcoded 'air_pistol').
  // Default 'air_pistol' preserves S-02 behavior.
  const [targetType, setTargetType] = useState<string>(TARGET_TYPES[0]);

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

      <div className={styles.field}>
        <label htmlFor="weapon_type">Weapon type</label>
        <select
          id="weapon_type"
          value={weaponType}
          onChange={(e) => setWeaponType(e.target.value)}
        >
          {WEAPON_TYPES.map((w) => (
            <option key={w} value={w}>{w}</option>
          ))}
        </select>
      </div>

      <div className={styles.field}>
        <label htmlFor="target_type">Target type</label>
        <select
          id="target_type"
          value={targetType}
          onChange={(e) => setTargetType(e.target.value)}
        >
          {TARGET_TYPES.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
      </div>

      <button
        type="button"
        className={styles.next}
        onClick={() => onNext({
          caliber,
          distance_m: distance,
          weapon_type: weaponType,
          target_type: targetType,
        })}
      >
        Next
      </button>
    </div>
  );
}
