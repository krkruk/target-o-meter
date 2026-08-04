// add-github-button-and-cookies — Phase 3 acceptance: the cookie consent banner.
//
// Closes what jsdom can't (per cookieConsent.test.ts:8-10, banner DOM is
// "covered by manual verification"): the banner is a library-owned portal
// appended to document.body, consent persists in localStorage across a real
// reload, and the "Cookie Policy" link performs real SPA->Django navigation
// to the standalone /privacy page. None of these are reachable by the
// component/integration layers.
//
// BOT-SUPPRESSION OVERRIDE: vanilla-cookieconsent v3.1.0 ships
// `hideFromBots: true` as a built-in default and treats
// `navigator.webdriver === true` (which every Playwright/Chromium sets) as a
// bot, silently suppressing the banner via an early return before any modal
// DOM is built — no error, no log. To exercise the REAL production config
// (hideFromBots stays true in the app — a desirable production behavior we do
// NOT weaken) against the same code real users get, the test masks
// navigator.webdriver to undefined in the context. This is test-scoped: it
// makes the test browser resemble a human visitor; it does not change the app.
// Masking webdriver is also how the library's own ecosystem handles it.
//
// Each test clears the cookieconsent localStorage key in beforeEach so the
// banner re-appears deterministically (Playwright isolates storage per
// context, but the library's own key could survive a run that set it; this
// makes first-visit behavior explicit and order-independent).
//
// Provenance: risks 3.3/3.4/3.5/3.8 from context/changes/add-github-button-and-cookies/plan.md.
// Seed conventions: dashboard-viewport.spec.ts + accept-flow.spec.ts (getByRole,
// expect().toBeVisible() wait-for-state, live SPA via globalSetup dev bypass).
import { test, expect, type Page } from '@playwright/test';

/**
 * vanilla-cookieconsent v3.1.0 stores consent in a COOKIE named `cc_cookie`
 * (its default `cookie.name`) — NOT in localStorage, which it leaves empty.
 * The stored value is a URL-encoded JSON blob carrying categories,
 * consentId, and timestamps. Clearing this cookie makes the library treat
 * the visit as first-visit and re-show the banner.
 */
const CC_COOKIE_NAME = 'cc_cookie';

/**
 * Clear the consent cookie so the banner re-appears as on first visit.
 * Called AFTER goto lands at the origin (so the cookie domain is set) but
 * BEFORE the cookieConsent init effect runs — goto returns once the HTML
 * shell is parsed, and the banner mounts a tick later when React + the
 * library initialize, so clearing here reliably precedes the library's read.
 */
async function clearConsent(page: Page): Promise<void> {
  await page.context().clearCookies({ name: CC_COOKIE_NAME });
}

test.describe('Cookie consent banner (add-github-button-and-cookies Phase 3)', () => {
  // Context-level: mask navigator.webdriver so vanilla-cookieconsent's
  // hideFromBots guard does not suppress the banner for the automated browser.
  // Applied once per context (runs before each page's first navigation).
  test.beforeEach(async ({ context }) => {
    await context.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    });
  });

  test('3.3/3.4 — first visit shows the banner and the Cookie Policy link navigates to /privacy', async ({ page }) => {
    // 3.3: banner appears on first visit (unauthenticated root).
    await page.goto('/');
    await clearConsent(page);

    // The consent modal is role=dialog + aria-modal=true + aria-labelledby
    // (vanilla-cookieconsent v3.1.0 sets these). The accessible name derives
    // from the title "We use cookies" (cookieConsent.ts). Waiting for the
    // dialog to be visible is the wait-for-state: no fixed timeout.
    const banner = page.getByRole('dialog', { name: /we use cookies/i });
    await expect(banner).toBeVisible({ timeout: 10_000 });

    // Accept and Reject are equally prominent (no dark pattern): both present.
    await expect(banner.getByRole('button', { name: /accept all/i })).toBeVisible();
    await expect(banner.getByRole('button', { name: /reject all/i })).toBeVisible();

    // 3.4: the "Cookie Policy" link resolves to the standalone /privacy page
    // (Phase 2 catch-all exclusion — NOT the SPA shell). The link lives in
    // the banner's description.
    const policyLink = banner.getByRole('link', { name: /cookie policy/i });
    await expect(policyLink).toHaveAttribute('href', '/privacy');
    await policyLink.click();

    // Navigated to /privacy: a standalone server-rendered page.
    await expect(page).toHaveURL(/\/privacy$/);

    // It must be the standalone page, not the SPA shell: no #root mount, and
    // the strictly-necessary cookie inventory is present. (Mirrors the
    // "not the SPA shell" assertions in tests/system/test_privacy_page.py.)
    await expect(page.locator('#root')).toHaveCount(0);
    const body = await page.locator('body').textContent() ?? '';
    expect(body).toMatch(/sessionid/i);
    expect(body).toMatch(/csrftoken/i);
    expect(body.toLowerCase()).toContain('no analytics');
  });

  test('3.5 — Reject dismisses the banner and consent persists across reload', async ({ page }) => {
    // First visit: banner appears.
    await page.goto('/');
    await clearConsent(page);
    const banner = page.getByRole('dialog', { name: /we use cookies/i });
    await expect(banner).toBeVisible({ timeout: 10_000 });

    // Reject: the banner dismisses and consent is recorded.
    await banner.getByRole('button', { name: /reject all/i }).click();
    await expect(banner).toBeHidden({ timeout: 5_000 });

    // Consent landed in the cc_cookie cookie (the library's persistence store).
    const stored = (await page.context().cookies()).find((c) => c.name === CC_COOKIE_NAME);
    expect(stored, 'cc_cookie should be set after Reject').toBeDefined();
    expect(stored!.value, 'cc_cookie should carry the necessary category').toContain('necessary');

    // 3.5: reload — the banner must NOT reappear. Because clearConsent() runs
    // only before the FIRST goto in this test, the consent cookie survives the
    // reload and the library reads valid consent -> no re-prompt. Asserting
    // absence here therefore proves the LIBRARY persisted consent, not that
    // storage was wiped. toBeHidden resolves immediately on absence; the
    // timeout is a ceiling, not a sleep.
    await page.reload();
    await expect(banner).toBeHidden({ timeout: 6_000 });
    const persisted = (await page.context().cookies()).find((c) => c.name === CC_COOKIE_NAME);
    expect(persisted, 'cc_cookie should still be present after reload').toBeDefined();
  });

  test('3.8 — banner mounts site-wide and does not break the authenticated SPA', async ({ page }) => {
    // The dev bypass (DEV_AUTH_BYPASS_SUB in globalSetup) makes /v1/me return
    // 200, so the SPA mounts AppShell + the dashboard. The banner must still
    // appear above it and must not interfere with the SPA boot.
    await page.goto('/dashboard');
    await clearConsent(page);

    // SPA booted: a dashboard region is visible (proves AppShell mounted).
    await expect(page.getByRole('region', { name: /hero stats/i })).toBeVisible({ timeout: 15_000 });

    // And the banner is present (site-wide mount from main.tsx).
    const banner = page.getByRole('dialog', { name: /we use cookies/i });
    await expect(banner).toBeVisible({ timeout: 10_000 });

    // Dismissing it does not disturb the SPA: dashboard region stays put.
    await banner.getByRole('button', { name: /reject all/i }).click();
    await expect(banner).toBeHidden({ timeout: 5_000 });
    await expect(page.getByRole('region', { name: /hero stats/i })).toBeVisible();
  });
});
