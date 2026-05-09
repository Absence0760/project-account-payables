import { expect, test } from '@playwright/test';

import { ACME_MANAGER, signInAndWait } from './fixtures/helpers';

/**
 * /exceptions — manager + admin can view. Seed creates 4 exceptions
 * per tenant (mix of open / resolved / etc).
 */

test.describe('/exceptions (acme manager)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page, ACME_MANAGER);
		await page.goto('/exceptions');
		await page.waitForLoadState('networkidle');
	});

	test('renders the page and the seeded exception cards', async ({ page }) => {
		await expect(page.getByRole('heading', { name: 'Exceptions' })).toBeVisible();
		// Each exception is an `.exception-card`. Seed = 4 per tenant.
		await expect(page.locator('.exception-card').first()).toBeVisible({
			timeout: 5_000
		});
		expect(await page.locator('.exception-card').count()).toBeGreaterThan(0);
	});

	test('summary chip totals are visible', async ({ page }) => {
		// `.summary-chips` only renders when summary !== null. Each chip
		// shows its label + count. The 'Open' chip is the default
		// active filter.
		await expect(page.locator('.summary-chips .chip', { hasText: /^Open\s/ })).toBeVisible({
			timeout: 5_000
		});
	});
});
