// S-03 Phase 7 acceptance: the accept → dashboard-update link.
//
// Continues past the S-02 scoring-flow spec (dashboard → upload → waiting →
// results) to cover the S-03 accept/reject surface:
//   - Happy path: results → Accept → /dashboard shows the updated hero stats +
//     a new recent-results entry (the accepted result landed + aggregated).
//   - Reject path: a fresh job → results → Reject → /dashboard shows NO new
//     entry (reject is the absence of a POST; FR-011).
//
// Reuses the vision domain's versioned fixture (12.jpg) + the seeded
// MockDetector (global-setup pins MOCK_DETECTOR_SEED + MOCK_DETECTOR_HOLE_COUNT).
// Mirrors the existing Playwright conventions (the globalSetup boots Django +
// Vite + qcluster).
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..', '..');
const FIXTURE_IMG = resolve(REPO_ROOT, 'src', 'domains', 'vision', 'tests', 'fixtures', '12.jpg');

test.describe('Accept flow end-to-end (S-03)', () => {
  test('happy path: results → Accept → /dashboard shows the accepted result', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto('/dashboard');

    // Capture the dashboard's starting total_shots so we can assert it grew.
    // The hero region renders after getAggregations resolves.
    const hero = page.getByRole('region', { name: /hero stats/i });
    await expect(hero).toBeVisible({ timeout: 15_000 });
    const beforeTotal = (await hero.textContent())?.match(/(\d+)/)?.[1];
    const beforeCount = beforeTotal ? Number(beforeTotal) : 0;

    // Drive the upload wizard to a succeeded result.
    await page.getByRole('button', { name: /add photos/i }).click();
    await expect(page).toHaveURL(/\/upload$/);
    await page.getByRole('combobox', { name: /caliber/i }).selectOption('9x19mm');
    await page.getByRole('combobox', { name: /distance/i }).selectOption('25');
    await page.getByRole('combobox', { name: /weapon type/i }).selectOption('sport_pistol');
    await page.getByRole('button', { name: /next/i }).click();

    await page.getByLabel(/select a photo of your target/i).setInputFiles({
      name: 'target.jpg', mimeType: 'image/jpeg', buffer: readFileSync(FIXTURE_IMG),
    });
    await expect(page).toHaveURL(/\/waiting\//, { timeout: 15_000 });
    await expect(page).toHaveURL(/\/results\//, { timeout: 60_000 });

    // Apply a hole correction, then Accept.
    await expect(page.getByRole('img', { name: /marked target/i })).toBeVisible();
    const holeSelects = page.locator('select[id^="correct-"]');
    await expect(holeSelects).toHaveCount(5);
    await holeSelects.first().selectOption('9');
    await page.getByRole('button', { name: /accept result/i }).click();

    // Accept navigates to /dashboard.
    await expect(page).toHaveURL(/\/dashboard$/, { timeout: 15_000 });

    // The dashboard's hero total_shots grew (the accepted result's holes landed +
    // aggregated: +5 for the 5-hole mock pattern).
    await expect(hero).toBeVisible();
    await expect(async () => {
      const afterText = (await hero.textContent()) ?? '';
      const afterCount = Number(afterText.match(/(\d+)/)?.[1] ?? 0);
      expect(afterCount).toBe(beforeCount + 5);
    }).toPass({ timeout: 15_000 });

    // The recent-results list shows a new entry (the accepted result).
    const results = page.getByRole('region', { name: /^results$/i });
    await expect(results).toBeVisible();
    await expect(results.locator('ul li')).toHaveCount(1, { timeout: 10_000 });
  });

  test('reject path: results → Reject → /dashboard shows NO new entry', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto('/dashboard');

    // Capture the starting recent-results count (empty for a fresh user).
    const results = page.getByRole('region', { name: /^results$/i });
    await expect(results).toBeVisible({ timeout: 15_000 });
    const beforeRows = await results.locator('ul li').count();

    // Drive a fresh job to results, then Reject.
    await page.getByRole('button', { name: /add photos/i }).click();
    await page.getByRole('combobox', { name: /caliber/i }).selectOption('9x19mm');
    await page.getByRole('button', { name: /next/i }).click();
    await page.getByLabel(/select a photo of your target/i).setInputFiles({
      name: 'target.jpg', mimeType: 'image/jpeg', buffer: readFileSync(FIXTURE_IMG),
    });
    await expect(page).toHaveURL(/\/results\//, { timeout: 60_000 });
    await expect(page.getByRole('img', { name: /marked target/i })).toBeVisible();

    // Reject → /dashboard, no POST (FR-011: reject is the absence of persistence).
    await page.getByRole('button', { name: /reject result/i }).click();
    await expect(page).toHaveURL(/\/dashboard$/, { timeout: 15_000 });

    // The recent-results count is unchanged (no AcceptedResult was created).
    await expect(results).toBeVisible();
    await expect(results.locator('ul li')).toHaveCount(beforeRows);
  });
});
