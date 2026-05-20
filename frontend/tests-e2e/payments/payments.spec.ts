import { expect, test } from '../fixtures/helpers';

/**
 * /payments — three-tab surface (Queue / History / Runs) plus a
 * summary panel. Seed creates 1 payment run with 3 payments and 4
 * exceptions, so History and Runs both render content.
 */

test.describe('/payments (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/payments');
		await page.waitForLoadState('networkidle');
	});

	test('summary cards render', async ({ page }) => {
		await expect(page.getByRole('heading', { name: 'Payments' })).toBeVisible();

		// `.scard` blocks are populated from /api/payments/summary. We
		// don't assert on the dollar amounts (seed data may evolve);
		// the contract is "summary cards present and labelled".
		const labels = page.locator('.scard-label');
		await expect(labels.first()).toBeVisible({ timeout: 5_000 });
		expect(await labels.count()).toBeGreaterThanOrEqual(1);
	});

	test('Queue tab is active by default', async ({ page }) => {
		await expect(page.locator('.tab', { hasText: 'Queue' })).toHaveClass(/active/);
	});

	test('History tab shows seeded payments', async ({ page }) => {
		await page.locator('.tab', { hasText: 'History' }).click();

		// 3 payments in the seed → table renders rows. The History tab
		// is the only one with the search "Search payments..." input.
		await expect(page.getByPlaceholder('Search payments...')).toBeVisible();
		await expect(page.locator('table tbody tr').first()).toBeVisible({
			timeout: 5_000
		});
	});

	test('Runs tab shows the seeded payment run', async ({ page }) => {
		await page.locator('.tab', { hasText: 'Runs' }).click();

		// Seed creates 1 run per tenant, status "completed". Click into
		// the row → the RunDetailModal opens.
		const firstRow = page.locator('table tbody tr.clickable').first();
		await expect(firstRow).toBeVisible({ timeout: 5_000 });

		await firstRow.click();
		const modal = page.locator('div.modal[role="dialog"][aria-label="Payment run"]');
		await expect(modal).toBeVisible();
		await expect(modal.locator('h2')).toHaveText('Payment Run');
		// status badge is rendered with a class equal to the status —
		// completed for the seeded run.
		await expect(modal.locator('.status-badge')).toBeVisible();

		// Close it. The modal has two "Close" controls — an X icon
		// (close-btn, aria-label="Close") and a footer Cancel button
		// labelled "Close". Click the X to be unambiguous.
		await modal.locator('.close-btn').click();
		await expect(modal).toBeHidden();
	});
});
