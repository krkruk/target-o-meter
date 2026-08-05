// ui-chores Phase 4: chart 2-decimal formatting contract. The YAxis ticks and
// Tooltip values must read at exactly 2 decimals (e.g. 8.67, not 8.6777777).
// The formatters are pure functions; they're unit-tested directly rather than
// via recharts internals (jsdom renders the chart at width 0, so asserting on
// rendered ticks is brittle).
import { describe, it, expect } from 'vitest';
import { formatChartTick, formatChartTooltip } from './DailyAverageChart';

describe('DailyAverageChart formatters', () => {
  describe('formatChartTick (YAxis tickFormatter)', () => {
    it('formats a whole number tick to 2 decimals', () => {
      expect(formatChartTick(0)).toBe('0.00');
      expect(formatChartTick(10)).toBe('10.00');
    });

    it('formats a fractional tick to exactly 2 decimals (rounds)', () => {
      expect(formatChartTick(7.3333)).toBe('7.33');
      expect(formatChartTick(7.6666)).toBe('7.67');
    });

    it('coerces string-typed values recharts may hand it', () => {
      // Number(v) guard: recharts occasionally passes ticks as strings.
      expect(formatChartTick('4' as unknown as number)).toBe('4.00');
    });
  });

  describe('formatChartTooltip (Tooltip formatter)', () => {
    it('formats the hovered value to exactly 2 decimals', () => {
      expect(formatChartTooltip(7.3333)).toBe('7.33');
      expect(formatChartTooltip(9)).toBe('9.00');
    });

    it('coerces string-tipped values', () => {
      expect(formatChartTooltip('8.5' as unknown as number)).toBe('8.50');
    });
  });
});
