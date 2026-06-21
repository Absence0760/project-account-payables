import { expect, test } from '../fixtures/helpers';

/**
 * Dashboard recoverable-error state.
 *
 * Regression for the silent dead-end: a failed `GET /api/dashboard` used
 * to leave `loading=false` with `data=null` and no `{:else}` branch, so
 * the single most-visited page rendered a blank shell with no error, no
 * retry, no toast. This spec forces the dashboard fetch to fail and asserts
 * the page surfaces an error + a Retry control, then recovers when the
 * next fetch succeeds.
 */
test.describe('dashboard error state', () => {
	test('failed load shows an error + retry, and retry recovers', async ({ page }) => {
		// Fail only the FIRST dashboard fetch; let the retry through.
		let failed = false;
		await page.route('**/api/dashboard', async (route) => {
			if (!failed) {
				failed = true;
				await route.fulfill({ status: 500, body: '{"detail":"boom"}' });
			} else {
				await route.continue();
			}
		});

		await page.goto('/');

		// Error branch is visible (not a blank page). Scope to the dashboard's
		// own error block — the global Toast live-regions also carry role="alert".
		const errorBox = page.locator('.dashboard-error');
		await expect(errorBox).toBeVisible();
		const retry = errorBox.getByRole('button');
		await expect(retry).toBeVisible();

		// Retrying succeeds → the real dashboard renders (KPI cards) and the
		// error block is gone.
		await retry.click();
		await expect(page.locator('.kpi').first()).toBeVisible();
		await expect(page.locator('.dashboard-error')).toHaveCount(0);
	});
});
