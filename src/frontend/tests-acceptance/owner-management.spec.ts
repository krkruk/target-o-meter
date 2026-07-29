// S-04 acceptance: the owner user-management UI flow.
//
// Closes the rows that jsdom + component tests could only cover piecewise —
// the real SPA against Django + Vite, driving the Admin link → /admin list →
// Ban modal → "Active ban" chip → Unban → "Banned before" chip → Delete
// modal → row gone. The dev-bypass user (auth0|playwright-acceptance) IS the
// owner (OWNER_SUB_ID matches), so the Admin link is visible. A plain target
// user (auth0|acceptance-target, nick "target-acct") is seeded by global-setup.
//
// The real-Auth0 ban round-trip (4.7) is out of scope for automated
// acceptance — it needs live Auth0 creds; the mocked-callback system test
// (tests/system/test_ban_enforcement.py) covers the enforcement logic.
import { test, expect } from '@playwright/test';

test.describe('Owner user management end-to-end (S-04)', () => {
  test('Admin link → list → ban → chip → unban → delete', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto('/dashboard');

    // The dev-bypass user is the owner → the Admin link is present.
    const adminLink = page.getByRole('menuitem', { name: /admin/i });
    await expect(adminLink).toBeVisible({ timeout: 10_000 });
    await adminLink.click();
    await expect(page).toHaveURL(/\/admin$/);

    // The list renders with the seeded target user.
    const list = page.getByRole('region', { name: /user administration/i });
    await expect(list).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('target-acct')).toBeVisible();

    // Ban the target row.
    const row = page.locator('[data-row]', { hasText: 'target-acct' });
    await row.getByRole('button', { name: /^ban$/i }).click();
    await expect(page.getByRole('dialog', { name: /ban target-acct/i })).toBeVisible();

    await page.getByRole('textbox', { name: /reason/i }).fill('acceptance test ban reason');
    await page.getByRole('combobox', { name: /duration/i }).selectOption('7d');
    await page.getByRole('button', { name: /confirm ban/i }).click();

    // The row now shows the active-ban chip + an Unban button.
    await expect(row.getByText(/active ban/i)).toBeVisible();
    await expect(row.getByRole('button', { name: /unban/i })).toBeVisible();

    // Unban → chip becomes "Banned before".
    await row.getByRole('button', { name: /unban/i }).click();
    await expect(row.getByText(/banned before/i)).toBeVisible();

    // Delete the row.
    await row.getByRole('button', { name: /delete/i }).click();
    await expect(page.getByRole('dialog', { name: /delete target-acct/i })).toBeVisible();
    // The Auth0 reminder note is present.
    await expect(page.getByText(/cannot do that for you/i)).toBeVisible();
    await page.getByRole('button', { name: /delete permanently/i }).click();

    // The row is gone.
    await expect(page.getByText('target-acct')).toHaveCount(0, { timeout: 10_000 });
  });

  test('the owner row has no Ban/Delete buttons', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto('/admin');

    const list = page.getByRole('region', { name: /user administration/i });
    await expect(list).toBeVisible({ timeout: 10_000 });
    // The owner (acceptance-runner) appears in the list but with no action buttons.
    const ownerRow = page.locator('[data-row][data-owner="true"]').first();
    await expect(ownerRow).toBeVisible();
    await expect(ownerRow.getByRole('button')).toHaveCount(0);
  });
});
