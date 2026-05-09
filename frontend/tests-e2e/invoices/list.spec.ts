import { expect, test } from '@playwright/test';

import { signInAndWait } from '../fixtures/helpers';

/**
 * /invoices — the core surface of the app. The seed creates 10 acme
 * invoices, so a successful render must show at least one row in the
 * table. This is a load-bearing smoke: a regression that breaks the
 * /api/invoices listing or the table rendering would trip here.
 */

test.describe('/invoices (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
	});

	test('lists at least one seeded invoice', async ({ page }) => {
		await page.goto('/invoices');
		await page.waitForLoadState('networkidle');

		// Toolbar's search input is the layout anchor — confirms the
		// page mounted.
		await expect(
			page.getByPlaceholder('Search invoices...')
		).toBeVisible();

		// `.filter-chip` is the All/<status> filter row; the "All" chip
		// shows the total count. Seed gives us 10, but assert >= 1 so a
		// future seed change doesn't break the spec for an unrelated
		// reason.
		const allChip = page.locator('.filter-chip', { hasText: /^All\s+\d+/ }).first();
		await expect(allChip).toBeVisible();

		// At least one invoice row. The exact row markup may evolve;
		// the contract is "table renders rows from /api/invoices".
		const rows = page.locator('table tbody tr');
		await expect(rows.first()).toBeVisible({ timeout: 10_000 });
		expect(await rows.count()).toBeGreaterThan(0);
	});

	test('search input is interactive', async ({ page }) => {
		await page.goto('/invoices');
		await page.waitForLoadState('networkidle');

		const search = page.getByPlaceholder('Search invoices...');
		await search.fill('vendor');
		await expect(search).toHaveValue('vendor');
	});
});
