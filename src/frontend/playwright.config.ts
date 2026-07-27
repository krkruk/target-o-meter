// Playwright config for the Target-o-meter SPA acceptance tests.
//
// Boots the full dev stack (Django runserver + Vite + django-q2 worker) in a
// project-local clean context under results/playwright-<run-id>/, then drives
// the rendered SPA against it with Chromium. The global setup owns the
// subprocess lifecycle; the baseURL points at Django (:8000), which serves
// the SPA shell + the /v1 API and proxies the JS bundle to Vite (:5173).
//
// What this closes that jsdom can't: real browser layout (the dashboard's
// viewport-locked no-scroll grid at 1920x1080 / 1366x768) + the full SPA
// navigation chain (dashboard -> /upload -> /waiting -> /results) against the
// real WSGI + Vite + q2 stack.
//
// Run with: npx playwright test
// (the globalSetup boots the stack; tests run once it's ready).
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests-acceptance',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  // Single worker: the globalSetup boots one shared stack; parallel workers
  // would each need their own (and their own DB + ports). Acceptance tests are
  // end-to-end and don't benefit from parallelism.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: process.env.SPA_ACCEPTANCE_BASE_URL || 'http://127.0.0.1:8187',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  globalSetup: './tests-acceptance/global-setup.ts',
  globalTeardown: './tests-acceptance/global-teardown.ts',
});
