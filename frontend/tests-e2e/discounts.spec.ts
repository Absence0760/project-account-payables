import { expect, test } from './fixtures/helpers';

/**
 * /discounts — Dynamic Discounting & Early-Payment Optimization dashboard.
 *
 * Reads GET /api/discounts/dashboard + /offers, POSTs /optimize and the
 * accept/decline actions. The default per-worker storage state signs the
 * worker's admin in, and admin is one of the allowed roles (admin /
 * ap_manager / cfo), so the page loads directly without a redirect.
 *
 * NOTE: the Phase-C `/api/discounts` router isn't wired yet, so these calls
 * may currently 404. The page is built to degrade gracefully (zeroed KPIs +
 * empty offers table), and this spec asserts the *structure* + the
 * empty-state fallback rather than seeded tallies. When the backend + a seed
 * land, the offer-row + accept-flow assertions below activate automatically
 * (they branch on whether any row rendered).
 */

test.describe('/discounts (admin)', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/discounts');
		await page.waitForLoadState('networkidle');
	});

	test('renders the discounts surface — header, KPIs, optimizer, filters, table', async ({
		page
	}) => {
		await expect(page.getByRole('heading', { name: 'Discounts' })).toBeVisible();

		// KPI row: Captured / Missed / Capture rate / Projected savings / Open offers.
		await expect(page.locator('.kpi').first()).toBeVisible({ timeout: 10_000 });
		await expect(page.locator('.kpi')).toHaveCount(5);
		await expect(page.locator('.kpi-label', { hasText: 'Capture rate' })).toBeVisible();

		// Optimizer panel with its cash-budget input + Optimize button.
		await expect(page.getByRole('heading', { name: 'Early-payment optimizer' })).toBeVisible();
		await expect(page.getByLabel('Cash budget')).toBeVisible();
		await expect(page.getByRole('button', { name: 'Optimize' })).toBeVisible();

		// Status filter chips.
		await expect(page.locator('.filter-chip', { hasText: 'All' })).toBeVisible();
		await expect(page.locator('.filter-chip', { hasText: 'Captured' })).toBeVisible();
		await expect(page.locator('.filter-chip', { hasText: 'Missed' })).toBeVisible();

		// The offers data table renders (rows or the centred empty state).
		await expect(page.locator('.grid-container table')).toBeVisible();
	});

	test('switching the status filter re-requests the offers list', async ({ page }) => {
		const respPromise = page.waitForResponse(
			(r) => r.url().includes('/api/discounts/offers') && r.url().includes('status=captured')
		);
		await page.locator('.filter-chip', { hasText: 'Captured' }).click();
		const resp = await respPromise;
		// The backend may not be wired yet (404 acceptable); we only assert the
		// request was issued with the right status param.
		expect(resp.request().url()).toContain('status=captured');
	});

	test('clicking Optimize posts to /optimize', async ({ page }) => {
		const respPromise = page.waitForResponse((r) =>
			r.url().includes('/api/discounts/optimize')
		);
		await page.getByLabel('Cash budget').fill('100000');
		await page.getByRole('button', { name: 'Optimize' }).click();
		const resp = await respPromise;
		expect(resp.request().method()).toBe('POST');
	});

	test('offers table: either rows render with an Accept action, or the empty state shows', async ({
		page
	}) => {
		await expect(page.locator('.grid-container table')).toBeVisible({ timeout: 10_000 });

		const rows = page.locator('.grid-container tbody tr:not(:has(td.empty))');
		const rowCount = await rows.count();

		if (rowCount === 0) {
			// No seeded offers (or router not wired) → friendly empty state.
			await expect(page.locator('td.empty')).toBeVisible();
			await expect(page.locator('td.empty')).toContainText(/No discount offers|Loading offers/);
			return;
		}

		// Base-amount cell carries a currency symbol, not a bare number.
		await expect(rows.first().locator('td.right .money').first()).toContainText(/[^\d.,]/);

		// If an `offered` row exists, its Accept action opens the tier modal.
		const accept = page.getByRole('button', { name: /^Accept discount for / }).first();
		if (await accept.count()) {
			await accept.click();
			await expect(page.getByRole('dialog', { name: 'Accept discount offer' })).toBeVisible();
		}
	});
});

test.describe('/discounts (clerk — not authorized)', () => {
	// Opt out of the default admin storage state so we can sign in as the clerk.
	test.use({ storageState: { cookies: [], origins: [] } });

	test('ap_clerk is redirected away from the dashboard', async ({ page, tenantClerk }) => {
		// ap_clerk is not in the allowed set (admin / ap_manager / cfo). Sign in
		// fresh as the clerk, then confirm the gated page bounces to the dashboard.
		await page.goto('/login');
		await page.waitForLoadState('networkidle');
		await page.locator('input[type="email"]').fill(tenantClerk.email);
		await page.locator('input[type="password"]').fill(tenantClerk.password);
		await page.locator('form button[type="submit"]').click();
		await page.waitForURL(/^http:\/\/[^/]+:7777\/?$/, { timeout: 15_000 });

		await page.goto('/discounts');
		// The page waits for /me then redirects clerks to the tenant root.
		await page.waitForURL(/^http:\/\/[^/]+:7777\/?$/, { timeout: 15_000 });
		await expect(page.getByRole('heading', { name: 'Discounts' })).toHaveCount(0);
	});
});
