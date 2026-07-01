import { expect, test } from '../fixtures/helpers';

/**
 * /tax — 1099 vendor reporting dashboard.
 *
 * Reads GET /api/tax/1099-report?year=. Default storage state signs the
 * worker's admin in, so the page loads directly.
 *
 * Report shape note: the backend returns *every* vendor (outer-joined to
 * payments), so the row set is the tenant's vendor list and changing the
 * year re-aggregates each vendor's YTD/payment-count rather than adding
 * or removing rows. The table is therefore only ever empty when the
 * tenant has no vendors; the "no match" empty state is reachable via a
 * search/filter that nothing satisfies. We assert structure + the
 * year-scoped request + the search empty-state, not exact tallies (the
 * lean e2e seed leaves is_1099_eligible=False, so reportable counts can
 * legitimately be zero).
 */

test.describe('/tax (admin)', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/tax');
		await page.waitForLoadState('networkidle');
	});

	test('renders the 1099 surface — KPIs, year selector, filters, table', async ({ page }) => {
		await expect(page.getByRole('heading', { name: '1099 Reporting' })).toBeVisible();

		// KPI summary row (4 cards) populated from the report summary.
		await expect(page.locator('.kpi').first()).toBeVisible({ timeout: 10_000 });
		await expect(page.locator('.kpi')).toHaveCount(4);

		// Year selector defaults to the current calendar year.
		const yearSelect = page.getByLabel('Tax year');
		await expect(yearSelect).toBeVisible();
		await expect(yearSelect).toHaveValue(String(new Date().getFullYear()));

		// Filter chips + the data table are present.
		await expect(page.locator('.filter-chip', { hasText: 'All' })).toBeVisible();
		await expect(page.locator('.filter-chip', { hasText: 'Missing W-9' })).toBeVisible();
		await expect(page.locator('.grid-container table')).toBeVisible();

		// Seeded tenant has vendors → at least one row, no empty placeholder.
		await expect(page.locator('.grid-container tbody tr').first()).toBeVisible({ timeout: 10_000 });
		await expect(page.locator('td.empty')).toHaveCount(0);
	});

	test('switching the tax year re-requests the report for that year', async ({ page }) => {
		const respPromise = page.waitForResponse(
			(r) => r.url().includes('/api/tax/1099-report') && r.url().includes('year=2024')
		);
		await page.getByLabel('Tax year').selectOption('2024');
		const resp = await respPromise;
		expect(resp.ok()).toBeTruthy();
		// Vendor rows still render (year re-aggregates YTD per vendor).
		await expect(page.locator('.grid-container tbody tr').first()).toBeVisible({ timeout: 10_000 });
	});

	test('YTD cells render with a currency symbol, not a bare number', async ({ page }) => {
		const firstYtd = page.locator('.grid-container tbody tr').first().locator('td').last();
		await expect(firstYtd).toBeVisible({ timeout: 10_000 });
		// USD report → "$" + grouped/decimal formatting from Intl.NumberFormat
		// (e.g. "$0.00" or "$1,234.50"). A raw number would lack the symbol.
		await expect(firstYtd).toContainText(/\$[\d,]+\.\d{2}/);
	});

	test('money follows the report currency, not a hardcoded USD', async ({ page }) => {
		// The report response is now authoritative for the display currency (it
		// carries the org's reporting/home currency the totals are denominated
		// in). Patch it to a non-USD currency and assert the money follows. A
		// fresh page per test → no leak into the USD-default tests above.
		await page.route('**/api/tax/1099-report**', async (route) => {
			const resp = await route.fetch();
			const body = await resp.json();
			body.currency = 'EUR';
			await route.fulfill({ response: resp, json: body });
		});

		await page.goto('/tax');
		await page.waitForLoadState('networkidle');

		// The Total-Reportable KPI (4th card) and every per-vendor YTD cell now
		// render in EUR (€) from the report's currency — a hardcoded USD fallback
		// would show "$".
		const totalReportable = page.locator('.kpi').nth(3).locator('.kpi-value');
		await expect(totalReportable).toBeVisible({ timeout: 10_000 });
		await expect(totalReportable).toContainText('€');
		await expect(totalReportable).not.toContainText('$');

		const firstYtd = page.locator('.grid-container tbody tr').first().locator('td').last();
		await expect(firstYtd).toBeVisible({ timeout: 10_000 });
		await expect(firstYtd).toContainText('€');
		await expect(firstYtd).not.toContainText('$');
	});

	test('a no-match search shows the empty state', async ({ page }) => {
		await expect(page.locator('.grid-container tbody tr').first()).toBeVisible({ timeout: 10_000 });
		await page.getByLabel('Search vendors').fill('zzz-no-such-vendor-zzz');
		// Client-side filter → centred empty row with the no-match copy.
		await expect(page.locator('td.empty')).toBeVisible();
		await expect(page.locator('td.empty')).toContainText('No vendors match this filter.');
	});
});
