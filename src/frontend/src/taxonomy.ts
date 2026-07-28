// S-02 Phase 8 + S-03: UI-only taxonomy for the capture/upload wizard.
//
// The BFF treats caliber as free-text `caliber_hint`; S-03 promoted `distance`
// to a real ScoringJob column and added `weapon_type` (FR-009). These lists
// live client-side only. The taxonomy bugs (`.32ACP` missing, `.22LR`/`9x19mm`
// silent DEFAULT fallback, `Slug` split-brain in the vision domain) remain
// deferred — `caliber_hint` stays free-text; the detector ignores it.
//
// Distances are ISSF-aligned (10m air pistol, 25m/50m precision) plus the
// broader list from the brief.

export const CALIBERS = [
  '.22LR',
  '9x19mm',
  '.223Rem',
  '.32ACP',
  '7.62x39',
  'Slug',
] as const;

export const DISTANCES_M = [7, 15, 25, 50, 100, 200, 300, 500] as const;

// S-03 FR-009: weapon_type + target_type. weapon_type is a UI suggestion list
// (the BFF accepts it as free-text); target_type is the two ISSF types vision
// supports (the BFF's Literal enforces these two on the wire).
export const WEAPON_TYPES = ['air_pistol', 'sport_pistol', 'free_pistol', 'revolver'] as const;
export const TARGET_TYPES = ['air_pistol', 'precision_pistol'] as const;

export type Caliber = (typeof CALIBERS)[number];
export type DistanceM = (typeof DISTANCES_M)[number];
export type WeaponType = (typeof WEAPON_TYPES)[number];
export type TargetType = (typeof TARGET_TYPES)[number];

