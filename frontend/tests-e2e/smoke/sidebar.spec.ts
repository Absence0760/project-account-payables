import { expect, test } from '../fixtures/helpers';

/**
 * Shared side panel (the left nav `Sidebar`, rendered in every authenticated
 * route via `+layout.svelte`).
 *
 * The profile popover is an overlay menu opened from the sidebar footer. It
 * dismisses on backdrop click — but used to ignore Escape (its backdrop carried
 * a no-op `onkeydown`), stranding a keyboard user who opened it. These assert
 * both dismissal paths and that Escape restores focus to the trigger.
 *
 * Read-only (no mutation) → parallel-safe across worker tenants.
 */
test.describe('sidebar profile popover', () => {
	test('Escape closes the popover and restores focus to the trigger', async ({ page }) => {
		await page.goto('/');
		await expect(page.locator('aside.sidebar')).toBeVisible();

		await page.locator('.profile-btn').click();
		await expect(page.locator('.profile-popover')).toBeVisible();

		await page.keyboard.press('Escape');
		await expect(page.locator('.profile-popover')).toBeHidden();

		// Focus must land back on the trigger, not be lost to <body> — so the
		// keyboard user keeps their place in the nav.
		await expect(page.locator('.profile-btn')).toBeFocused();
	});

	test('backdrop click still closes the popover', async ({ page }) => {
		await page.goto('/');
		await page.locator('.profile-btn').click();
		await expect(page.locator('.profile-popover')).toBeVisible();

		await page.locator('.profile-backdrop').click();
		await expect(page.locator('.profile-popover')).toBeHidden();
	});
});
