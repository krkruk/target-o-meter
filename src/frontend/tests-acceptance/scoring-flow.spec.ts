// S-02 Phase 8 acceptance: the full capture/upload -> waiting -> results flow.
//
// Closes the rows that jsdom + component tests could only cover piecewise:
//   - 8.5 (desktop): dashboard -> /upload -> caliber+distance -> file picker
//     -> /waiting/:jobId (polls) -> /results/:jobId (5 mocked holes + the
//     marked image + per-hole correction dropdowns).
//   - 8.6 (mobile, <=760px): dashboard -> /upload (gallery "Choose file"
//     input, no capture attribute) -> same waiting -> results chain.
//     fix/add-missing-warning-and-gallery-button-in-mobile: mobile no longer
//     routes to /capture; both viewports now go through /upload.
//
// Drives the live SPA against Django + Vite + the qcluster worker. The worker
// runs process_image with VISION_DETECTOR=mock, so the job transitions
// queued -> running -> succeeded. S-03: the MockDetector now emits a random
// N-hole pattern; global-setup pins MOCK_DETECTOR_SEED + MOCK_DETECTOR_HOLE_COUNT
// (=5) so the hole-count assertions here are deterministic. The waiting screen
// polls until terminal; /results renders the marked image + dropdowns.
//
// The file upload uses a real image (the vision domain's versioned 12.jpg
// fixture), fetched from the repo via the test fixture path.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..', '..');
const FIXTURE_IMG = resolve(REPO_ROOT, 'src', 'domains', 'vision', 'tests', 'fixtures', '12.jpg');

// The full flow is identical on desktop (/upload) and mobile (/capture) once
// the media-acquisition step is reached — both render a file input (mobile
// adds `capture`). Drive both under one describe, parametrized by viewport.
test.describe('Scoring flow end-to-end', () => {
  test('8.5 — desktop: dashboard -> /upload -> caliber+distance -> waiting -> results (5 mocked holes)', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto('/dashboard');

    // Add-photos on desktop routes to /upload.
    await page.getByRole('button', { name: /add photos/i }).click();
    await expect(page).toHaveURL(/\/upload$/);

    // Fill the wizard step.
    await page.getByRole('combobox', { name: /caliber/i }).selectOption('9x19mm');
    await page.getByRole('combobox', { name: /distance/i }).selectOption('25');
    await page.getByRole('button', { name: /next/i }).click();

    // The file picker is now visible — upload the fixture image.
    const fileInput = page.getByLabel(/select a photo of your target/i);
    await fileInput.setInputFiles({
      name: 'target.jpg',
      mimeType: 'image/jpeg',
      buffer: readFileSync(FIXTURE_IMG),
    });

    // Navigates to /waiting/:jobId and polls. Wait for the terminal transition
    // to /results/:jobId (the qcluster worker drives process_image to
    // succeeded). Generous timeout: the worker + pipeline take a few seconds.
    await expect(page).toHaveURL(/\/waiting\//, { timeout: 15_000 });
    await expect(page).toHaveURL(/\/results\//, { timeout: 60_000 });

    // Results render: the marked image + 5 per-hole correction dropdowns
    // (MockDetector's seeded random pattern, count pinned via global-setup).
    // S-03 adds a 4-field confirm-params form (also comboboxes), so scope the
    // hole-count assertion to the hole-correction selects (id^="correct-").
    await expect(page.getByRole('img', { name: /marked target/i })).toBeVisible();
    await expect(page.locator('select[id^="correct-"]')).toHaveCount(5);
  });

  test('8.6 — mobile: dashboard -> /upload -> gallery picker -> waiting -> results', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/dashboard');

    // fix/add-missing-warning-and-gallery-button-in-mobile: add-photos on
    // mobile now routes to /upload (not /capture). /upload exposes the
    // "Choose file" gallery input (no capture attribute) + "Take a picture"
    // camera input (capture="environment"). Drive the gallery input so the
    // flow exercises the gallery path on a mobile viewport. (True device
    // emulation for the routing regression itself lives in
    // mobile-upload.spec.ts.)
    await page.getByRole('button', { name: /add photos/i }).click();
    await expect(page).toHaveURL(/\/upload$/);

    // Wizard step.
    await page.getByRole('combobox', { name: /caliber/i }).selectOption('.22LR');
    await page.getByRole('button', { name: /next/i }).click();

    // The PII warning renders on mobile too (regression guard).
    await expect(page.getByRole('note')).toBeVisible();

    // The gallery ("Choose file") input has NO capture attribute — picking it
    // opens the photo gallery, not the camera. Upload through it.
    const fileInput = page.getByLabel(/select a photo of your target/i);
    await expect(fileInput).not.toHaveAttribute('capture');
    await fileInput.setInputFiles({
      name: 'target.jpg',
      mimeType: 'image/jpeg',
      buffer: readFileSync(FIXTURE_IMG),
    });

    // Same waiting -> results transition.
    await expect(page).toHaveURL(/\/waiting\//, { timeout: 15_000 });
    await expect(page).toHaveURL(/\/results\//, { timeout: 60_000 });
    await expect(page.getByRole('img', { name: /marked target/i })).toBeVisible();
    // S-03: scope to the hole-correction selects (the confirm-params form adds more).
    await expect(page.locator('select[id^="correct-"]')).toHaveCount(5);
  });

  test('8.8 — refresh on /waiting/:jobId resumes polling until terminal', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto('/dashboard');
    await page.getByRole('button', { name: /add photos/i }).click();
    await page.getByRole('button', { name: /next/i }).click();
    await page.getByLabel(/select a photo of your target/i).setInputFiles({
      name: 'target.jpg',
      mimeType: 'image/jpeg',
      buffer: readFileSync(FIXTURE_IMG),
    });
    await expect(page).toHaveURL(/\/waiting\//, { timeout: 15_000 });

    // Refresh mid-poll — the waiting screen re-mounts and resumes polling.
    await page.reload();
    await expect(page).toHaveURL(/\/waiting\//);
    // It still resolves to /results after the refresh.
    await expect(page).toHaveURL(/\/results\//, { timeout: 60_000 });
  });

  test('8.9 — refresh on /results/:jobId re-fetches and re-renders', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto('/dashboard');
    await page.getByRole('button', { name: /add photos/i }).click();
    await page.getByRole('button', { name: /next/i }).click();
    await page.getByLabel(/select a photo of your target/i).setInputFiles({
      name: 'target.jpg',
      mimeType: 'image/jpeg',
      buffer: readFileSync(FIXTURE_IMG),
    });
    await expect(page).toHaveURL(/\/results\//, { timeout: 60_000 });
    await expect(page.getByRole('img', { name: /marked target/i })).toBeVisible();

    // Refresh — results re-fetch + re-render.
    await page.reload();
    await expect(page.getByRole('img', { name: /marked target/i })).toBeVisible();
    // S-03: scope to the hole-correction selects.
    await expect(page.locator('select[id^="correct-"]')).toHaveCount(5);
  });
});
