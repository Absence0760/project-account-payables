import { expect, test } from '../fixtures/helpers';

/**
 * `/positive-pay` "Items exported" / "Returns flagged" KPIs.
 *
 * Both reduced over the LOADED page while "Positive Pay files" showed the
 * server's whole-set total, so the three cards contradicted each other past
 * page 1. They now read `GET /api/positive-pay/summary` — whole filtered set,
 * summed `item_count` and `meta.return_summary` figures, same file_type/status
 * filters as the list.
 */

function ppFile(n: number, itemCount = 5) {
	return {
		id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
		file_type: 'check_issue',
		bank_format: 'csv',
		status: 'generated',
		payment_run_id: `00000000-0000-4000-8000-${String(n + 500).padStart(12, '0')}`,
		item_count: itemCount,
		total_amount: '1000.00',
		currency: 'USD',
		account_last4: '4567',
		meta: null,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z'
	};
}

test('the positive-pay KPIs are the whole-set rollup, not the loaded page', async ({ page }) => {
	await page.route('**/api/positive-pay**', async (route) => {
		const url = new URL(route.request().url());
		if (url.pathname === '/api/positive-pay/summary') {
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					total: 9,
					by_status: { generated: 7, returned_processed: 2 },
					items_exported: 143,
					returns_flagged: 6
				})
			});
		}
		if (url.pathname === '/api/positive-pay') {
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					items: [ppFile(1), ppFile(2)],
					total: 9,
					page: 1,
					page_size: 50
				})
			});
		}
		return route.continue();
	});

	await page.goto('/positive-pay');

	const kpiRow = page.locator('.kpi-row');
	const files = kpiRow.locator('.kpi', {
		has: page.getByText('Positive Pay files', { exact: true })
	});
	const items = kpiRow.locator('.kpi', { has: page.getByText('Items exported', { exact: true }) });
	const returns = kpiRow.locator('.kpi', { has: page.getByText('Returns flagged', { exact: true }) });

	await expect(files.locator('.kpi-value')).toHaveText('9');
	// whole-set sums (143 / 6), not the 10 / 0 the two loaded rows would give
	await expect(items.locator('.kpi-value')).toHaveText('143');
	await expect(returns.locator('.kpi-value')).toHaveText('6');
});
