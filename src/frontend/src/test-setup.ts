// Vitest global setup: register @testing-library/jest-dom matchers
// (toBeInTheDocument, toHaveTextContent, …) once for every test file.
import '@testing-library/jest-dom/vitest';

// jsdom doesn't implement ResizeObserver (S-02 Phase 7: recharts'
// ResponsiveContainer depends on it). A no-op stub lets the chart render its
// SVG in the jsdom test environment; layout-dependent sizing isn't asserted on
// (jsdom can't compute layout anyway).
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: typeof ResizeObserverStub }).ResizeObserver =
  ResizeObserverStub;
