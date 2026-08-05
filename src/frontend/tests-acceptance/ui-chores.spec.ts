// ui-chores acceptance — one Playwright test capturing all five UI changes.
//
// Provenance: /10x-e2e for context/changes/ui-chores/plan.md. The plan's five
// phases each carry Manual verification rows (2.6-2.8, 3.6-3.8, 4.5-4.6,
// 5.6-5.9) that jsdom can't close (CSS :hover/:focus-within, real layout /
// media queries, recharts SVG ticks, the verbatim PII wording). This spec
// drives them against the live stack in a single journey:
//
//   1. seed one scoring job (so /scores + the home "Recent results" list and
//      the daily-average chart each have content to assert against);
//   2. TopBar: hover/focus the nick -> the Logout menu reveals (Phase 2);
//   3. /scores + home / "Recent results": each action button carries an icon
//      node + text label; Delete picks up the danger color (Phase 3);
//   4. home / chart: YAxis ticks render at 2 decimals (Phase 4);
//   5. /upload desktop: verbatim PII callout, only "Choose file" visible;
//      /upload mobile (<=760px): both buttons stack vertically and the camera
//      input carries capture="environment"; /capture fallback still resolves
//      (Phase 5).
//
// The dev bypass (DEV_AUTH_BYPASS_SUB in globalSetup) auths the session on the
// first page load — no storageState / UI login. The mock detector pins a
// 5-hole pattern, so the seeded job produces a deterministic result row.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..', '..');
const FIXTURE_IMG = resolve(REPO_ROOT, 'src', 'domains', 'vision', 'tests', 'fixtures', '12.jpg');

// Verbatim PII warning (user decision: "keep my original" — do not paraphrase).
const PII_WARNING =
  'The data is used to train LLM models. Do not upload Personal Identifiable Information. ' +
  'By uploading the image, you agree to effectively make this information public. ' +
  'Think about it and proceed responsibly.';

// Uploads + ACCEPTS one scoring job so it persists as a result row (a job
// alone doesn't appear in /scores or "Recent results" until it's accepted).
// Called once at the top of the journey so downstream route assertions have
// deterministic content. Returns to /dashboard after accept.
async function seedOneScore(page: import('@playwright/test').Page) {
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto('/dashboard');
  await page.getByRole('button', { name: /add photos/i }).click();
  await expect(page).toHaveURL(/\/upload$/);
  await page.getByRole('button', { name: /next/i }).click();
  await page.getByLabel(/select a photo of your target/i).setInputFiles({
    name: 'target.jpg',
    mimeType: 'image/jpeg',
    buffer: readFileSync(FIXTURE_IMG),
  });
  await expect(page).toHaveURL(/\/results\//, { timeout: 60_000 });
  await expect(page.getByRole('img', { name: /marked target/i })).toBeVisible();
  // Accept persists the result (POST /v1/scoring/results) and returns to
  // /dashboard, where the new row is aggregated.
  await page.getByRole('button', { name: /accept result/i }).click();
  await expect(page).toHaveURL(/\/dashboard$/, { timeout: 15_000 });
}

test.describe('ui-chores — all five UI changes (one journey)', () => {
  test('TopBar dropdown, icon buttons, 2-decimal chart, PII warning + camera button', async ({ page }) => {
    // ── Seed: one scoring job so the lists + chart have content. ──────────
    await seedOneScore(page);

    // ── Phase 2: TopBar logout dropdown (hover + keyboard + no layout shift).
    await page.goto('/dashboard');
    const banner = page.getByRole('banner');
    // The nick renders as a <button> trigger carrying aria-haspopup="menu".
    const nickTrigger = banner.getByRole('button', { name: 'acceptance-runner' });
    await expect(nickTrigger).toHaveAttribute('aria-haspopup', 'menu');
    await expect(nickTrigger).toBeVisible();
    // The menu + Logout menuitem exist in the DOM; CSS hides them until the
    // group is hovered or focused. Hover reveals them.
    const logoutMenuitem = banner.getByRole('menuitem', { name: /logout/i }).first();
    await expect(logoutMenuitem).toBeHidden();
    await nickTrigger.hover();
    await expect(logoutMenuitem).toBeVisible();
    // Keyboard: focusing the trigger (Tab) reveals the menu via :focus-within.
    await nickTrigger.focus();
    await expect(logoutMenuitem).toBeVisible();

    // ── Phase 3: icon-ify action buttons — /scores (ScoreDashboard). ──────
    await page.goto('/scores');
    // Wait for the row list to populate (the seeded job becomes a result row).
    const previewBtn = page.getByRole('button', { name: /preview score from/i }).first();
    await expect(previewBtn).toBeVisible({ timeout: 15_000 });
    const modifyBtn = page.getByRole('button', { name: /modify score from/i }).first();
    const deleteBtn = page.getByRole('button', { name: /delete score from/i }).first();
    // Preview leads with an <img> (the immutable target.svg); Modify/Delete
    // lead with an inline <svg> (react-icons). Text labels stay.
    await expect(previewBtn.locator('img')).toHaveCount(1);
    await expect(previewBtn).toContainText(/preview/i);
    await expect(modifyBtn.locator('svg')).toHaveCount(1);
    await expect(modifyBtn).toContainText(/modify/i);
    await expect(deleteBtn.locator('svg')).toHaveCount(1);
    await expect(deleteBtn).toContainText(/delete/i);
    // Delete's svg inherits the danger color (currentcolor -> --color-danger).
    const deleteColor = await deleteBtn.evaluate(
      (el) => getComputedStyle(el.querySelector('svg')!).color,
    );
    // --color-danger is #b91c1c -> rgb(185, 28, 28).
    expect(deleteColor.replace(/\s+/g, '')).toMatch(/rgb\(185,\s*28,\s*28\)/i);

    // ── Phase 3 (propagation): home / "Recent results" reuses <ScoreRow>. ─
    await page.goto('/');
    const homePreview = page.getByRole('button', { name: /preview score from/i }).first();
    await expect(homePreview).toBeVisible({ timeout: 15_000 });
    await expect(homePreview.locator('img')).toHaveCount(1);
    await expect(
      page.getByRole('button', { name: /modify score from/i }).first().locator('svg'),
    ).toHaveCount(1);
    await expect(
      page.getByRole('button', { name: /delete score from/i }).first().locator('svg'),
    ).toHaveCount(1);

    // ── Phase 4: chart YAxis ticks render at exactly 2 decimals. ──────────
    const chartRegion = page.getByRole('region', { name: /daily average/i });
    await expect(chartRegion).toBeVisible();
    // The chart's role="img" wrapper means recharts rendered (data present).
    await expect(chartRegion.getByRole('img')).toBeVisible();
    // recharts renders axis ticks as <text class="recharts-cartesian-axis-tick-value">.
    // That class is shared by the XAxis (dates) and the YAxis (numbers); filter
    // to the numeric YAxis ticks — each must read "<digits>.<2 digits>".
    const allTicks = chartRegion.locator('text.recharts-cartesian-axis-tick-value');
    await expect(allTicks.first()).toBeVisible();
    const tickTexts = (await allTicks.allTextContents())
      .map((t) => t.trim())
      .filter((t) => /\d/.test(t));
    // Keep only the numeric ticks (YAxis); drop date strings like "2026-08-05".
    const yAxisTicks = tickTexts.filter((t) => !t.includes('-'));
    expect(yAxisTicks.length, 'expected at least one YAxis tick').toBeGreaterThan(0);
    for (const text of yAxisTicks) {
      expect(text, `YAxis tick "${text}" must be 2 decimals`).toMatch(/^\d+\.\d{2}$/);
    }

    // ── Phase 5: /upload desktop — PII callout + only "Choose file". ──────
    await page.goto('/upload');
    await page.getByRole('button', { name: /next/i }).click();
    // Verbatim warning inside a role="note" callout.
    const note = page.getByRole('note');
    await expect(note).toBeVisible();
    expect((await note.innerText()).replace(/\s+/g, ' ').trim()).toBe(PII_WARNING);
    // Both buttons exist in the DOM; "Choose file" is visible, "Take a picture"
    // is hidden at >760px (CSS .mobileOnly { display: none }).
    const chooseBtn = page.getByRole('button', { name: /choose file/i });
    const cameraBtn = page.getByRole('button', { name: /take a picture/i });
    await expect(chooseBtn).toBeVisible();
    await expect(cameraBtn).toBeHidden();

    // ── Phase 5: /upload mobile (<=760px) — both visible, stacked, capture. ─
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(cameraBtn).toBeVisible();
    await expect(chooseBtn).toBeVisible();
    // The .uploadActions group switches to column at the 760px breakpoint.
    // It's the nearest common ancestor of the two buttons (the camera button
    // sits inside a .mobileOnly wrapper, so walk up two levels to the group).
    const actionsFlex = await page.evaluate(() => {
      const camBtn = Array.from(document.querySelectorAll('button')).find((b) =>
        /take a picture/i.test(b.textContent || ''))!;
      // .mobileOnly (parent) -> .uploadActions (grandparent).
      const group = camBtn.parentElement!.parentElement!;
      return getComputedStyle(group).flexDirection;
    });
    expect(actionsFlex).toBe('column');
    // The camera input carries capture="environment"; the file input does not.
    const cameraInput = page.locator('input[type="file"][capture="environment"]');
    const fileInput = page.locator('input[type="file"]:not([capture])');
    await expect(cameraInput).toHaveCount(1);
    await expect(fileInput).toHaveCount(1);

    // ── Phase 5: /capture fallback route still resolves. ──────────────────
    await page.goto('/capture');
    await expect(page.getByRole('combobox', { name: /caliber/i })).toBeVisible();
  });
});
