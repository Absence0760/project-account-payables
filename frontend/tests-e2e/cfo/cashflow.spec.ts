import { expect, signInAndWait, test } from '../fixtures/helpers';

/**
 * /cfo — Predictive cash-flow forecasting dashboard.
 *
 * Default storage state signs the worker's admin in, so admin reaches
 * the page. The CFO surface is gated to admin + cfo: an ap_clerk must
 * neither see the Cash Flow nav item nor render the page content.
 *
 * The forecast/what-if/cash-position panels are driven by the seeded
 * tenant's invoices + payment schedules, so we assert structure (cards,
 * controls, table) rather than exact dollar amounts, which the seed may
 * evolve.
 */

test.describe('/cfo (admin)', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/cfo');
		await page.waitForLoadState('networkidle');
	});

	test('renders the forecast surface with KPI cards and controls', async ({ page }) => {
		await expect(page.getByRole('heading', { name: 'Cash Flow' })).toBeVisible();
		// KPI row populated from /cashflow_forecast + /cashflow_whatif.
		await expect(page.locator('.kpi').first()).toBeVisible({ timeout: 10_000 });
		await expect(page.locator('.kpi')).toHaveCount(4);
		// Granularity + horizon segmented controls present.
		await expect(page.locator('.seg-btn', { hasText: 'week' })).toBeVisible();
	});

	test('toggling granularity re-requests and keeps the chart', async ({ page }) => {
		await expect(page.locator('.chart-card h2', { hasText: /Projected outflows/ })).toBeVisible({
			timeout: 10_000
		});
		const respPromise = page.waitForResponse(
			(r) => r.url().includes('/api/analytics/cashflow_forecast') && r.url().includes('granularity=month')
		);
		await page.locator('.seg-btn', { hasText: 'month' }).click();
		await respPromise;
		await expect(page.locator('.chart-card h2', { hasText: 'Projected outflows (month)' })).toBeVisible();
	});

	test('what-if scenario cards render with discount highlighted on early', async ({ page }) => {
		await expect(page.locator('.chart-card h2', { hasText: 'Payment-timing what-if' })).toBeVisible({
			timeout: 10_000
		});
		await expect(page.locator('.scenario-card')).toHaveCount(3);
		await expect(page.locator('.scenario-card.best .scenario-title')).toHaveText('Pay early');
	});

	test('entering an opening balance + threshold updates the cash position', async ({ page }) => {
		await expect(page.locator('.chart-card h2', { hasText: 'Cash position' })).toBeVisible({
			timeout: 10_000
		});
		const respPromise = page.waitForResponse((r) =>
			r.url().includes('/api/analytics/cash_position') && r.url().includes('opening_balance=500000')
		);
		await page.getByLabel('Opening bank balance').fill('500000');
		await page.getByLabel('Minimum balance threshold').fill('100000');
		const resp = await respPromise;
		expect(resp.status()).toBe(200);
		// The running-balance table renders rows with opening/closing columns.
		await expect(page.locator('.cf-table thead th', { hasText: 'Opening' })).toBeVisible();
	});

	test('Export CSV triggers a download', async ({ page }) => {
		const downloadPromise = page.waitForEvent('download');
		await page.getByTestId('export-csv').click();
		const download = await downloadPromise;
		expect(download.suggestedFilename()).toMatch(/^cashflow_forecast_.*\.csv$/);
	});
});

test.describe('/cfo RBAC', () => {
	test.use({ storageState: { cookies: [], origins: [] } });

	// Cash Flow lives inside the folded "Insights" sidebar group; it surfaces as
	// a section tab on the group's pages, not as its own sidebar row.
	test('ap_clerk cannot reach Cash Flow (no sidebar row, no section tab)', async ({
		page,
		tenantClerk
	}) => {
		await signInAndWait(page, tenantClerk);
		await page.goto('/assistant'); // the clerk's Insights landing
		await expect(page.locator('aside.sidebar a.nav-item[href="/cfo"]')).toHaveCount(0);
		await expect(page.locator('a.section-tab[href="/cfo"]')).toHaveCount(0);
	});

	test('cfo opens Cash Flow via the Insights section tabs', async ({ page, tenantCfo }) => {
		await signInAndWait(page, tenantCfo);
		// The Insights group row lands on AI Assistant; Cash Flow is a tab there.
		await page.locator('aside.sidebar a.nav-item', { hasText: 'Insights' }).click();
		const tab = page.locator('a.section-tab[href="/cfo"]');
		await expect(tab).toBeVisible();
		await tab.click();
		await expect(page.getByRole('heading', { name: 'Cash Flow' })).toBeVisible();
	});
});
