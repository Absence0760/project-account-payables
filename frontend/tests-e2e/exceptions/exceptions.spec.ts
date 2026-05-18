import { expect, test, ACME_BASE } from '../fixtures/helpers';

import { ACME_MANAGER, signInAndWait } from '../fixtures/helpers';

// Pinned to the acme tenant: this spec uses ACME_*/TECHFLOW_* creds or
// asserts cross-tenant isolation that requires fixed tenant slugs. The
// per-worker baseURL from fixtures/helpers.ts would otherwise route to
// the wrong tenant. Multiple workers may share acme here — keep this
// file's tests read-only or idempotent.
test.use({ baseURL: ACME_BASE });

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
