import { expect, test } from '@playwright/test';

import { ACME_MANAGER, signInAndWait } from '../fixtures/helpers';

/**
 * /exceptions — manager + admin can view. Seed creates 4 exceptions
 * per tenant (mix of open / resolved / etc).
 *
 * Page is a dense table (was cards). Each row is a `<tr>` inside the
 * shared `.grid-container` shell.
 */

test.describe('/exceptions (acme manager)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page, ACME_MANAGER);
		await page.goto('/exceptions');
		await page.waitForLoadState('networkidle');
	});

	test('renders the page and the seeded exception rows', async ({ page }) => {
		await expect(page.getByRole('heading', { name: 'Exceptions' })).toBeVisible();
		const rows = page.locator('table tbody tr');
		await expect(rows.first()).toBeVisible({ timeout: 5_000 });
		expect(await rows.count()).toBeGreaterThan(0);
	});

	test('summary chip totals are visible', async ({ page }) => {
		await expect(page.locator('.filter-chip', { hasText: /^Open\s/ })).toBeVisible({
			timeout: 5_000
		});
	});
});
