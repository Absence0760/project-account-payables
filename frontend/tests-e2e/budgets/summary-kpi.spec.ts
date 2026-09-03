import { expect, test } from '../fixtures/helpers';

/**
 * `/budgets` "Total allocated" KPI.
 *
 * The card used to `budgets.reduce(...)` over the LOADED page and render the
 * result in the org default currency — so it summed one page while the
 * "Budgets" count beside it was the server's whole-set total, and it added
 * EUR + USD into one meaningless figure. It now reads
 * `GET /api/budgets/summary`, which counts the whole filtered set and groups
 * the allocation by currency (never a cross-currency sum).
 *
 * Both list + summary responses are stubbed so the assertion doesn't depend on
 * what the shard's tenant happens to hold.
 */

function budget(n: number, currency = 'USD') {
	return {
		id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
		name: `E2E Budget ${n}`,
		dimension: 'department',
		dimension_value: `Dept ${n}`,
		period: '2026',
		period_start: null,
		period_end: null,
		amount: 1000,
		currency,
		notes: null,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z'
	};
}

test('the allocation KPI is the whole-set rollup, grouped by currency', async ({ page }) => {
	await page.route('**/api/budgets**', async (route) => {
		const url = new URL(route.request().url());
		if (url.pathname === '/api/budgets/summary') {
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					total: 9,
					by_currency: [
						{ currency: 'EUR', total: '4000.00', count: 4 },
						{ currency: 'USD', total: '5000.00', count: 5 }
					]
				})
			});
		}
		if (url.pathname === '/api/budgets') {
			// Only the first page's worth of rows — fewer than the whole set.
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					items: [budget(1, 'USD'), budget(2, 'EUR')],
					total: 9,
					page: 1,
					page_size: 50
				})
			});
		}
		return route.continue();
	});

	await page.goto('/budgets');

	const kpiRow = page.locator('.kpi-row');
	const allocated = kpiRow.locator('.kpi', { hasText: /Total allocated/i });

	// Headline = the first currency subtotal, formatted in its OWN currency —
	// NOT a sum of the two loaded rows (which would be 2 000) and NOT one
	// blended number.
	await expect(allocated.locator('.kpi-value')).toHaveText(/€|EUR/);
	await expect(allocated.locator('.kpi-value')).toContainText('4,000');
	// The other currency rides the muted sub-line, never dropped, never added.
	await expect(allocated.locator('.kpi-sub')).toContainText('5,000');

	// The "Budgets" count is the server's whole-set total (9), and the
	// allocation card no longer contradicts it by summing only what's loaded.
	const countCard = kpiRow.locator('.kpi').filter({ hasText: 'Budgets' });
	await expect(countCard.locator('.kpi-value')).toHaveText('9');
});
