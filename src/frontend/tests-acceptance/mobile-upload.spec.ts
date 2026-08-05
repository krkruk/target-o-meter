// fix/add-missing-warning-and-gallery-button-in-mobile — the mobile regression.
//
// Provenance: /10x-e2e (standalone). Risk: on a real phone, the Dashboard's
// "Add photos" button used to route to /capture, which (a) had no PII warning
// and (b) rendered a single native <input type="file" capture="environment">
// that mobile browsers label "Choose File" and open straight to the rear
// camera — so the gallery picker the user expected was unreachable. This spec
// protects the fix: mobile now routes to /upload, the PII warning renders, and
// the "Choose file" input has NO capture attribute (gallery) while "Take a
// picture" keeps capture="environment" (camera).
//
// Uses REAL device emulation (devices['Pixel 5']) — per the Playwright
// emulation docs that sets a mobile viewport (393x727), isMobile, hasTouch,
// and a mobile user agent TOGETHER. setViewportSize alone (used elsewhere in
// the suite) only resizes the viewport; it does not flip isMobile/hasTouch/UA,
// so it could not catch a UA- or isMobile-conditional regression. Pixel 5 is
// used (not iPhone 13) because its descriptor targets the chromium engine —
// the only one installed in this environment/CI. test.use() scopes the
// emulation to this file only — the rest of the suite runs desktop.
//
// Auth: the dev bypass (DEV_AUTH_BYPASS_SUB in globalSetup) auths the session
// on the first page load — no storageState / UI login. See seed.spec.ts for
// the conventions.
import { test, expect, devices } from '@playwright/test';

// Verbatim PII warning (user decision: "keep my original" — do not paraphrase).
// Kept as a literal here so this spec is a real oracle against wording drift.
const PII_WARNING =
  'The data is used to train LLM models. Do not upload Personal Identifiable Information. ' +
  'By uploading the image, you agree to effectively make this information public. ' +
  'Think about it and proceed responsibly.';

// Scope real device emulation to this file only — the rest of the suite stays
// desktop. Pixel 5: chromium-based, 393x727, isMobile + hasTouch + mobile UA.
test.use({ ...devices['Pixel 5'] });

test.describe('mobile upload entry point (Pixel 5 emulation)', () => {
  test('a mobile user reaches /upload (not /capture), sees the PII warning, and the Choose-file input opens the gallery (no capture)', async ({ page }) => {
    // ── The core regression: mobile Add-photos routes to /upload. ──────────
    await page.goto('/dashboard');
    await page.getByRole('button', { name: /add photos/i }).click();
    // The old behavior routed to /capture. If that branch returns, this fails.
    await expect(page).toHaveURL(/\/upload$/);

    // Advance the caliber/distance wizard to mount the upload step.
    await page.getByRole('button', { name: /next/i }).click();

    // ── The PII warning renders on mobile (was missing under the old /capture
    // route). Verbatim text inside a role="note" callout.
    const note = page.getByRole('note');
    await expect(note).toBeVisible();
    expect((await note.innerText()).replace(/\s+/g, ' ').trim()).toBe(PII_WARNING);

    // ── Both buttons are visible on mobile (<=760px). ──────────────────────
    const chooseBtn = page.getByRole('button', { name: /choose file/i });
    const cameraBtn = page.getByRole('button', { name: /take a picture/i });
    await expect(chooseBtn).toBeVisible();
    await expect(cameraBtn).toBeVisible();

    // ── "Choose file" → gallery (NO capture); "Take a picture" → camera ───
    // (capture="environment"). This is the heart of Case 2: the old /capture
    // surface exposed a single capture input the browser labelled "Choose
    // File" and opened to the camera. Here the gallery input must NOT carry
    // capture, and only the camera input carries capture="environment".
    const cameraInput = page.locator('input[type="file"][capture="environment"]');
    const fileInput = page.locator('input[type="file"]:not([capture])');
    await expect(cameraInput).toHaveCount(1);
    await expect(fileInput).toHaveCount(1);
  });
});
