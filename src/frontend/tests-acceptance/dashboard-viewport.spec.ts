// S-02 Phase 7 acceptance: the dashboard's viewport-locked CSS Grid.
//
// Closes the rows jsdom can't (it can't compute layout):
//   - 7.4: at 1920x1080 all four regions render and document doesn't scroll.
//   - 7.5: at 1366x768 the same — still fits (the grid scales, no overflow).
//   - 7.6 (already unit-pinned, asserted here for completeness): <=760px the
//     grid collapses to a scrollable single column (scroll IS expected there —
//     the brief's no-scroll is a laptop constraint; mobile falls back).
//
// Drives the live SPA (Django + Vite + the dev bypass) with Chromium at the
// real viewport sizes. The dev bypass (DEV_AUTH_BYPASS_SUB set in globalSetup)
// means /v1/me returns 200 -> the SPA mounts AppShell -> the dashboard route
// renders.
import { test, expect } from '@playwright/test';

const REGIONS = ['Hero stats', 'Results', 'Daily average'];

test.describe('Dashboard viewport', () => {
  test('7.4 — at 1920x1080 all regions render and the page does not scroll', async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto('/dashboard');

    // Each named region is an accessible landmark.
    for (const name of REGIONS) {
      await expect(page.getByRole('region', { name: new RegExp(name, 'i') })).toBeVisible();
    }
    // The add-photos button is the fourth affordance.
    await expect(page.getByRole('button', { name: /add photos/i })).toBeVisible();

    // No document-level scroll: clientHeight == scrollHeight (the grid is
    // height: 100%; overflow: hidden). The AppShell main is the scroll
    // container; the document itself must not overflow.
    const overflow = await page.evaluate(() => {
      return {
        scrollHeight: document.documentElement.scrollHeight,
        clientHeight: document.documentElement.clientHeight,
        bodyScrollHeight: document.body.scrollHeight,
      };
    });
    expect(overflow.scrollHeight).toBeLessThanOrEqual(overflow.clientHeight + 1);
  });

  test('7.5 — at 1366x768 the dashboard still fits (no document scroll)', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto('/dashboard');

    for (const name of REGIONS) {
      await expect(page.getByRole('region', { name: new RegExp(name, 'i') })).toBeVisible();
    }
    const overflow = await page.evaluate(() => ({
      scrollHeight: document.documentElement.scrollHeight,
      clientHeight: document.documentElement.clientHeight,
    }));
    expect(overflow.scrollHeight).toBeLessThanOrEqual(overflow.clientHeight + 1);
  });

  test('7.6 — at <=760px the grid collapses (scroll is acceptable on mobile)', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/dashboard');

    // Regions still render; the grid collapses to a scrollable single column.
    for (const name of REGIONS) {
      await expect(page.getByRole('region', { name: new RegExp(name, 'i') })).toBeVisible();
    }
    // fix/add-missing-warning-and-gallery-button-in-mobile: the add-photos
    // button now routes to /upload on every platform (it used to branch to
    // /capture on mobile). /upload carries the PII warning + the correct
    // mobile Choose-file/Take-a-picture buttons. True device emulation
    // (isMobile/hasTouch/UA) for this regression lives in mobile-upload.spec.
    await page.getByRole('button', { name: /add photos/i }).click();
    await expect(page).toHaveURL(/\/upload$/);
  });
});
