import { expect, test } from '../fixtures/helpers';

/**
 * `/requisitions` "Open total" + "Pending approval" KPIs.
 *
 * Both reduced over the LOADED page: `pendingCount` counted pending rows on
 * that page while the "Requisitions" card showed the server's whole-set total,
 * and `periodTotal` summed one page AND added values across currencies. They
 * now read `GET /api/requisitions/summary` — whole filtered set, grouped by
 * currency, with `by_status` counts.
 *
 * List + summary responses are stubbed so the assertion doesn't depend on the
 * shard tenant's contents.
 */

function requisition(n: number, status = 'draft', currency = 'USD') {
	return {
		id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
		requisition_number: `E2E-REQ-${n}`,
		title: `Req ${n}`,
		requester_user_id: '00000000-0000-4000-8000-000000000999',
		department: 'Engineering',
		status,
		needed_by: null,
		justification: null,
		vendor_id: null,
		contract_id: null,
		budget_id: null,
		total: 100,
		currency,
		notes: null,
		submitted_at: null,
		approved_at: null,
		approved_by: null,
		rejection_reason: null,
		converted_po_id: null,
		line_items: [],
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z'
	};
}

test('the requisition KPIs are the whole-set rollup, grouped by currency', async ({ page }) => {
	await page.route('**/api/requisitions**', async (route) => {
		const url = new URL(route.request().url());
		if (url.pathname === '/api/requisitions/summary') {
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					total: 12,
					by_status: { draft: 9, pending_approval: 3 },
					by_currency: [
						{ currency: 'EUR', total: '2000.00', count: 4 },
						{ currency: 'USD', total: '9000.00', count: 8 }
					]
				})
			});
		}
		if (url.pathname === '/api/requisitions') {
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					items: [requisition(1), requisition(2, 'pending_approval', 'EUR')],
					total: 12,
					page: 1,
					page_size: 50
				})
			});
		}
		return route.continue();
	});

	await page.goto('/requisitions');

	const kpiRow = page.locator('.kpi-row');

	// "Open total" headline is the first currency subtotal in its own currency —
	// not a sum of the two loaded rows and not one blended figure.
	const openTotal = kpiRow.locator('.kpi').filter({ hasText: /Open total/i });
	await expect(openTotal.locator('.kpi-value')).toContainText('2,000');
	await expect(openTotal.locator('.kpi-sub')).toContainText('9,000');

	// "Pending approval" is the whole-set by_status count (3), not the 1 pending
	// row on the loaded page.
	const pending = kpiRow.locator('.kpi').filter({ hasText: /Pending approval/i });
	await expect(pending.locator('.kpi-value')).toHaveText('3');

	// …and it no longer contradicts the whole-set "Requisitions" count (12).
	const count = kpiRow.locator('.kpi', { has: page.getByText('Requisitions', { exact: true }) });
	await expect(count.locator('.kpi-value')).toHaveText('12');
});
