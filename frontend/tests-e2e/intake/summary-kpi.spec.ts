import { expect, test } from '../fixtures/helpers';

/**
 * `/intake` "Open" / "In review" KPIs.
 *
 * Both filtered the LOADED page by status while the "Requests" card beside them
 * showed the server's whole-set total, so the three cards contradicted each
 * other past page 1. They now read `GET /api/intake/summary` — whole filtered
 * set, `by_status` counts, same status/type/search filters as the list.
 */

function intake(n: number, status = 'open') {
	return {
		id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
		request_number: `E2E-INTK-${n}`,
		title: `Ask ${n}`,
		request_type: 'software',
		status,
		requester_user_id: '00000000-0000-4000-8000-000000000999',
		estimated_amount: 1000,
		currency: 'USD',
		vendor_id: null,
		vendor_name: 'Acme',
		justification: 'x',
		form_data: null,
		converted_requisition_id: null,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z'
	};
}

test('the intake KPIs are the whole-set status counts, not the loaded page', async ({ page }) => {
	await page.route('**/api/intake**', async (route) => {
		const url = new URL(route.request().url());
		if (url.pathname === '/api/intake/summary') {
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					total: 15,
					by_status: { open: 8, in_review: 4, approved: 2, converted: 1 }
				})
			});
		}
		if (url.pathname === '/api/intake') {
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					items: [intake(1), intake(2, 'in_review')],
					total: 15,
					page: 1,
					page_size: 50
				})
			});
		}
		return route.continue();
	});

	await page.goto('/intake');

	const kpiRow = page.locator('.kpi-row');
	const requests = kpiRow.locator('.kpi', { has: page.getByText('Requests', { exact: true }) });
	const open = kpiRow.locator('.kpi', { has: page.getByText('Open', { exact: true }) });
	const review = kpiRow.locator('.kpi', { has: page.getByText('In review', { exact: true }) });

	await expect(requests.locator('.kpi-value')).toHaveText('15');
	// whole-set by_status counts (8 / 4), not the 1 / 1 on the loaded page
	await expect(open.locator('.kpi-value')).toHaveText('8');
	await expect(review.locator('.kpi-value')).toHaveText('4');
});
