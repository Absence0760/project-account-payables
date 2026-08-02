import { expect, signInAndWait, test } from '../fixtures/helpers';

/**
 * /cfo — "CFO metrics" section (`CfoMetrics.svelte`).
 *
 * `GET /api/analytics/cfo` computed DPO, cash conversion cycle, accruals,
 * supplier concentration, fraud-rate trend, and rebate yield correctly, but
 * had no frontend surface at all until this component. Found by
 * exploratory persona-driven testing (CFO persona); filed as #236.
 *
 * The seeded tenant's data drives real numbers, so this spec asserts
 * structure (section renders, KPI cards populate, subsections render) rather
 * than exact figures, which the seed may evolve.
 */

test.describe('/cfo CFO-metrics section (admin)', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/cfo');
		await page.waitForLoadState('networkidle');
	});

	test('renders KPI cards and the accruals + concentration subsections', async ({ page }) => {
		const section = page.getByTestId('cfo-metrics-section');
		await expect(section).toBeVisible({ timeout: 10_000 });
		await expect(section.getByRole('heading', { name: 'CFO metrics' })).toBeVisible();

		// Four KPI cards: DPO, cash conversion cycle, AP balance, rebate yield.
		await expect(section.locator('.kpi')).toHaveCount(4);
		await expect(section.locator('.kpi', { hasText: 'Days payable outstanding' })).toBeVisible();
		await expect(section.locator('.kpi', { hasText: 'Cash conversion cycle' })).toBeVisible();
		await expect(section.locator('.kpi', { hasText: 'Accounts payable balance' })).toBeVisible();
		await expect(section.locator('.kpi', { hasText: 'Card rebate yield' })).toBeVisible();

		await expect(section.locator('h3', { hasText: 'Accruals' })).toBeVisible();
		await expect(section.locator('.cfm-stat', { hasText: 'Open POs' })).toBeVisible();
		await expect(section.locator('.cfm-stat', { hasText: 'Total accrual' })).toBeVisible();

		await expect(section.locator('h3', { hasText: 'Supplier concentration' })).toBeVisible();
		await expect(section.locator('.cfm-stat', { hasText: 'Top 10 vendors' })).toBeVisible();
	});

	test('re-fetches CFO metrics when the horizon control changes', async ({ page }) => {
		const section = page.getByTestId('cfo-metrics-section');
		await expect(section).toBeVisible({ timeout: 10_000 });

		const respPromise = page.waitForResponse(
			(r) => r.url().includes('/api/analytics/cfo') && r.url().includes('period_days=180')
		);
		await page.locator('.seg-btn', { hasText: '180d' }).click();
		const resp = await respPromise;
		expect(resp.status()).toBe(200);
	});
});

test.describe('/cfo CFO-metrics section RBAC', () => {
	test.use({ storageState: { cookies: [], origins: [] } });

	test('ap_clerk cannot reach the CFO-metrics section', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);
		await page.goto('/assistant'); // the clerk's Insights landing
		await expect(page.locator('aside.sidebar a.nav-item[href="/cfo"]')).toHaveCount(0);
	});

	test('cfo sees the CFO-metrics section', async ({ page, tenantCfo }) => {
		await signInAndWait(page, tenantCfo);
		await page.locator('aside.sidebar a.nav-item', { hasText: 'Insights' }).click();
		const tab = page.locator('a.section-tab[href="/cfo"]');
		await expect(tab).toBeVisible();
		await tab.click();
		await expect(page.getByTestId('cfo-metrics-section')).toBeVisible({ timeout: 10_000 });
	});
});
