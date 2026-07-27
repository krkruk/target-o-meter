// S-02 Phase 7: mocked dashboard fixtures.
//
// One typed fixture module so the dashboard's mocked data is centralized and
// S-03 can swap it for real API calls cleanly (a one-line import change per
// consumer). The types here mirror what the eventual S-03 aggregation API
// will return — the swap is a data-source change, not a shape change.
//
// Aggregation (FR-009/FR-010/FR-011/FR-012) is S-03 scope; S-02's dashboard
// chart + results read from these fixtures.

export interface HeroStats {
  totalShots: number;
  lastSessionAverage: number | null;
  bestResult: number | null;
}

export interface ResultSummary {
  jobId: string;
  date: string;        // ISO date
  score: number;       // 0-100 (the average of the session)
  targetCount: number; // # of targets in the session
}

export interface DailyAverage {
  date: string;        // ISO date (YYYY-MM-DD)
  average: number;     // 0-10 average for the day
}

export const mockHeroStats: HeroStats = {
  totalShots: 1247,
  lastSessionAverage: 8.4,
  bestResult: 9.7,
};

export const mockResults: ResultSummary[] = [
  { jobId: '11111111-1111-1111-1111-111111111111', date: '2026-07-26', score: 84, targetCount: 5 },
  { jobId: '22222222-2222-2222-2222-222222222222', date: '2026-07-24', score: 79, targetCount: 4 },
  { jobId: '33333333-3333-3333-3333-333333333333', date: '2026-07-22', score: 91, targetCount: 6 },
];

// 30 daily data points for the past month (deterministic — mocked, no
// aggregation backend yet).
export const mockDailyAverages: DailyAverage[] = Array.from(
  { length: 30 },
  (_, i) => {
    // Stable pseudo-pattern so the chart shape is deterministic across runs.
    const base = 7.5 + Math.sin(i / 3) * 1.2;
    const avg = Math.max(0, Math.min(10, Number(base.toFixed(1))));
    const d = new Date('2026-07-26');
    d.setDate(d.getDate() - (29 - i));
    return { date: d.toISOString().slice(0, 10), average: avg };
  },
);
