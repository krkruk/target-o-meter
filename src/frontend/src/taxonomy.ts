// S-02 Phase 8: UI-only taxonomy for the capture/upload wizard.
//
// The BFF treats caliber as free-text `caliber_hint` and distance as the mock
// `distance_m` field (no distance column on ScoringJob yet). These lists live
// client-side only — the user decided to use them as-is for S-02. The
// taxonomy bugs (`.32ACP` missing, `.22LR`/`9x19mm` silent DEFAULT fallback,
// `Slug` split-brain in the vision domain) are deferred to S-03 where they
// become load-bearing.
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

export type Caliber = (typeof CALIBERS)[number];
export type DistanceM = (typeof DISTANCES_M)[number];
