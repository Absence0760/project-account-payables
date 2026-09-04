import { expect, test } from '../fixtures/helpers';

/**
 * /discounts — every money figure is labelled with the currency the RESPONSE
 * says it is in, not the org-default store.
 *
 * `GET /api/discounts/dashboard` and `POST /api/discounts/optimize` each state
 * their own `currency` (the reporting currency their totals were summed in),
 * and the page already renders that field in the exclusion banners. The KPI row
 * and the optimizer summary nevertheless formatted through `orgCurrency`, a
 * separately-loaded store — so a tenant whose reporting currency had moved read
 * one figure under two different codes, two lines apart.
 *
 * Worse on the recommendation cards: `roi.savings` is computed from the
 * OFFER's base amount, so it is in the OFFER's currency, and `unconvertible` is
 * the response's statement that this row is NOT in the totals' currency. The
 * card stamped `orgCurrency` on it and never read the flag, so "Save $412.00"
 * could be €412 — sitting directly under a banner saying some offers are in
 * another currency, without saying which.
 *
 * Stubbed rather than seeded: the point is a response whose `currency` differs
 * from the org default, which no amount of tenant data guarantees.
 */

const ORG_CURRENCY = 'USD';
const RESPONSE_CURRENCY = 'EUR';

const DASHBOARD = {
	captured_count: 2,
	captured_amount: 500,
	missed_count: 1,
	missed_amount: 120,
	capture_rate_pct: 66,
	open_offer_count: 2,
	projected_savings: 890,
	currency: RESPONSE_CURRENCY,
	unconvertible_offer_count: 1,
	excluded_captured_count: 0,
	excluded_missed_count: 0
};

function roi(savings: number) {
	return {
		base_amount: 10_000,
		discount_percent: 2,
		days_accelerated: 20,
		savings,
		annualized_return_pct: 36.5,
		cost_of_capital_pct: 8,
		opportunity_cost: 40,
		net_benefit: savings - 40,
		worthwhile: true
	};
}

const OPTIMIZATION = {
	cash_budget: null,
	currency: RESPONSE_CURRENCY,
	cost_of_capital_pct: 8,
	total_savings_available: 612,
	total_savings_selected: 200,
	total_outlay_selected: 9800,
	unconvertible_count: 1,
	recommendations: [
		{
			offer_id: 'off-native-0001',
			invoice_id: null,
			vendor_id: null,
			vendor_name: 'Native Currency Vendor',
			invoice_number: 'NAT-1',
			tier_days: 10,
			discount_percent: 2,
			pay_by: '2026-01-15',
			roi: roi(200),
			selected: true,
			cumulative_outlay: 9800,
			unconvertible: false
		},
		{
			offer_id: 'off-foreign-0002',
			invoice_id: null,
			vendor_id: null,
			vendor_name: 'Foreign Currency Vendor',
			invoice_number: 'FOR-1',
			tier_days: 10,
			discount_percent: 2,
			pay_by: '2026-01-20',
			roi: roi(412),
			selected: false,
			cumulative_outlay: 9800,
			unconvertible: true
		}
	]
};

async function stubDiscounts(page: import('@playwright/test').Page): Promise<void> {
	// Pin the org-default store to a currency the responses are NOT in, so a
	// figure labelled from the store is distinguishable from one labelled off
	// the response. Exact-pathname guarded — `/api/organization/branding` and
	// friends must still reach the backend.
	await page.route('**/api/organization*', async (route) => {
		if (new URL(route.request().url()).pathname !== '/api/organization') {
			await route.continue();
			return;
		}
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ settings: { reporting_currency: ORG_CURRENCY } })
		});
	});
	await page.route('**/api/discounts/dashboard*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(DASHBOARD)
		})
	);
	await page.route('**/api/discounts/offers*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 20 })
		})
	);
	await page.route('**/api/discounts/optimize*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(OPTIMIZATION)
		})
	);
}

test.describe('/discounts response-stated currency', () => {
	test('the KPI row is labelled with the dashboard response’s own currency', async ({ page }) => {
		await stubDiscounts(page);
		await page.goto('/discounts');

		const kpis = page.locator('.kpi-row');
		await expect(kpis).toBeVisible({ timeout: 10_000 });
		// `projected_savings` = 890, stated in EUR by the very response the
		// banner below it quotes.
		await expect(kpis).toContainText('890');
		await expect(kpis).toContainText('€');
		await expect(kpis).not.toContainText('$');
	});

	test('the optimizer summary and a convertible card use the optimize response’s currency', async ({
		page
	}) => {
		await stubDiscounts(page);
		await page.goto('/discounts');

		await page.getByRole('button', { name: /Optimize/i }).click();

		const summary = page.locator('.opt-summary');
		await expect(summary).toBeVisible({ timeout: 10_000 });
		await expect(summary).toContainText('€');
		await expect(summary).not.toContainText('$');

		// A recommendation the backend did NOT flag is provably in the totals'
		// currency, so it can be labelled with it.
		const native = page.locator('.scenario-card', { hasText: 'Native Currency Vendor' });
		await expect(native).toContainText('€200');
		await expect(native).not.toContainText('$');
		await expect(native.getByTestId('rec-unconvertible')).toHaveCount(0);
	});

	test('an unconvertible card says so, and never stamps a currency on its savings', async ({
		page
	}) => {
		await stubDiscounts(page);
		await page.goto('/discounts');

		await page.getByRole('button', { name: /Optimize/i }).click();

		const foreign = page.locator('.scenario-card', { hasText: 'Foreign Currency Vendor' });
		await expect(foreign).toBeVisible({ timeout: 10_000 });

		// The flag reaches the CARD, not just the page-level banner: the reader
		// has to know which row's money is excluded, not only how many are.
		await expect(foreign.getByTestId('rec-unconvertible')).toBeVisible();
		await expect(foreign.getByTestId('rec-unconvertible')).toContainText(RESPONSE_CURRENCY);

		// The figure is real; the currency is not knowable from this response, so
		// no symbol is invented for it. "$412.00" here was the actual defect.
		const unlabelled = foreign.getByTestId('rec-savings-unlabelled');
		await expect(unlabelled).toHaveText(/^412[.,]00$/);
		await expect(foreign).not.toContainText('$412');
		await expect(foreign).not.toContainText('€412');
	});
});
