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
 * Each recommendation now STATES its currency, so a foreign row is labelled
 * with its own code and keeps the excluded-from-the-totals marker beside it. A
 * response predating that field still degrades to a symbol-free figure rather
 * than guessing — covered below, because the degradation is the contract.
 *
 * Stubbed rather than seeded: the point is a response whose `currency` differs
 * from the org default, which no amount of tenant data guarantees.
 */

const ORG_CURRENCY = 'USD';
const RESPONSE_CURRENCY = 'EUR';
// A third code, so a row labelled with its OWN currency is distinguishable from
// one labelled off the totals AND from one labelled off the org-default store.
const FOREIGN_CURRENCY = 'JPY';

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
			currency: RESPONSE_CURRENCY,
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
			// The row's own currency — the totals are in EUR, this money is not.
			currency: FOREIGN_CURRENCY,
			selected: false,
			cumulative_outlay: 9800,
			unconvertible: true
		}
	]
};

/** The same response as it arrived before `OptimizerRecommendation.currency`. */
const OPTIMIZATION_WITHOUT_ROW_CURRENCY = {
	...OPTIMIZATION,
	recommendations: OPTIMIZATION.recommendations.map(({ currency: _dropped, ...rest }) => rest)
};

async function stubDiscounts(
	page: import('@playwright/test').Page,
	optimization: unknown = OPTIMIZATION
): Promise<void> {
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
			body: JSON.stringify(optimization)
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

	test('an unconvertible card is labelled with its OWN currency and still says it is excluded', async ({
		page
	}) => {
		await stubDiscounts(page);
		await page.goto('/discounts');

		await page.getByRole('button', { name: /Optimize/i }).click();

		const foreign = page.locator('.scenario-card', { hasText: 'Foreign Currency Vendor' });
		await expect(foreign).toBeVisible({ timeout: 10_000 });

		// The figure carries the row's own code. "$412.00" (the org default) and
		// "€412.00" (the totals' currency) were both wrong for ¥412.
		await expect(foreign).toContainText('¥412');
		await expect(foreign).not.toContainText('$412');
		await expect(foreign).not.toContainText('€412');

		// …and the exclusion marker is still on the card, because being labelled
		// correctly is a different fact from being counted in the totals.
		await expect(foreign.getByTestId('rec-unconvertible')).toBeVisible();
		await expect(foreign.getByTestId('rec-unconvertible')).toContainText(RESPONSE_CURRENCY);
	});

	test('the cash budget goes out as the exact string that was typed', async ({ page }) => {
		// `Number()` on the way out was the defect: the server's `json.loads`
		// then handed the optimizer a float, so the budget it selected against
		// was the rounded double. This budget decides which invoices get paid
		// early. `POST /optimize` now 422s a JSON number, so the wire shape is
		// part of the contract, not a preference.
		const bodies: string[] = [];
		await stubDiscounts(page);
		await page.route('**/api/discounts/optimize*', async (route) => {
			bodies.push(route.request().postData() ?? '');
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(OPTIMIZATION)
			});
		});

		await page.goto('/discounts');
		// More precision than a double carries at this magnitude: `Number()` of
		// this is exactly 9800.
		await page.getByLabel('Cash budget').fill('9799.999999999999999');
		await page.getByRole('button', { name: /Optimize/i }).click();

		await expect(page.locator('.opt-summary')).toBeVisible({ timeout: 10_000 });
		expect(bodies).toHaveLength(1);
		expect(JSON.parse(bodies[0])).toEqual({ cash_budget: '9799.999999999999999' });
	});

	test('a budget that is not an amount is refused before it is sent', async ({ page }) => {
		// Running UNCONSTRAINED on a typo would commit more cash than was asked
		// for — the same silent-unconstrained failure the server's
		// `extra="forbid"` closes on its side.
		const bodies: string[] = [];
		await stubDiscounts(page);
		await page.route('**/api/discounts/optimize*', async (route) => {
			bodies.push(route.request().postData() ?? '');
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(OPTIMIZATION)
			});
		});

		await page.goto('/discounts');
		await page.getByLabel('Cash budget').fill('lots of it');
		await page.getByRole('button', { name: /Optimize/i }).click();

		await expect(page.getByText(/Enter the cash budget as a plain amount/)).toBeVisible();
		expect(bodies, 'nothing may be sent for input we could not read').toHaveLength(0);
	});

	test('a response without the per-row currency degrades to a bare figure', async ({ page }) => {
		// The field is additive; a client that broke without it would just be a
		// different way of getting the currency wrong.
		await stubDiscounts(page, OPTIMIZATION_WITHOUT_ROW_CURRENCY);
		await page.goto('/discounts');

		await page.getByRole('button', { name: /Optimize/i }).click();

		const foreign = page.locator('.scenario-card', { hasText: 'Foreign Currency Vendor' });
		await expect(foreign).toBeVisible({ timeout: 10_000 });
		const unlabelled = foreign.getByTestId('rec-savings-unlabelled');
		await expect(unlabelled).toHaveText(/^412[.,]00$/);
		await expect(foreign).not.toContainText('$412');
		await expect(foreign).not.toContainText('€412');

		// A row the old payload did NOT flag is still safely labelled with the
		// totals' currency, which it provably shares.
		const native = page.locator('.scenario-card', { hasText: 'Native Currency Vendor' });
		await expect(native).toContainText('€200');
	});
});
