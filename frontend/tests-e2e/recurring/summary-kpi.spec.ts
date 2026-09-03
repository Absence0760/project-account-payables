import { expect, test } from '../fixtures/helpers';

/**
 * `/recurring` KPI trio — "Active templates", "Next run", "Monthly recurring".
 *
 * All three derived from the LOADED page, and the monthly total divided floats
 * (`amount / 3`, `amount / 12`). They now read `GET /api/recurring/summary` —
 * whole filtered set, `by_status` counts, exact per-currency monthly-equivalent
 * spend, and the soonest upcoming run.
 *
 * Responses are stubbed so the assertion doesn't depend on the shard tenant.
 */

function template(n: number, status = 'active') {
	return {
		id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
		name: `E2E Template ${n}`,
		vendor_id: null,
		vendor_name: 'Acme',
		amount: 1000,
		currency: 'USD',
		cadence: 'monthly',
		day_of_period: 1,
		start_date: '2026-01-01',
		next_run_on: '2026-09-01',
		last_period_key: null,
		status,
		generated_count: 0,
		last_skip: null,
		variance_tolerance_pct: null,
		gl_account: '6000',
		cost_center: null,
		notes: null,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z'
	};
}

test('the recurring KPIs are the whole-set rollup with exact monthly-equivalent', async ({
	page
}) => {
	await page.route('**/api/recurring**', async (route) => {
		const url = new URL(route.request().url());
		if (url.pathname === '/api/recurring/summary') {
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					total: 20,
					by_status: { active: 14, paused: 4, ended: 2 },
					monthly_equivalent: [
						{ currency: 'EUR', total: '100.00', count: 1 },
						{ currency: 'USD', total: '5300.00', count: 13 }
					],
					soonest_next_run: '2026-09-01'
				})
			});
		}
		if (url.pathname === '/api/recurring') {
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					items: [template(1), template(2, 'paused')],
					total: 20,
					page: 1,
					page_size: 50
				})
			});
		}
		if (url.pathname === '/api/vendors') {
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ items: [] })
			});
		}
		return route.continue();
	});

	await page.goto('/recurring');

	const kpiRow = page.locator('.kpi-row');

	// "Active templates" is the whole-set by_status count (14), not the 1 active
	// row on the loaded page.
	const active = kpiRow.locator('.kpi').filter({ hasText: /Active templates/i });
	await expect(active.locator('.kpi-value')).toHaveText('14');

	// "Monthly recurring" headline is the first currency's exact monthly-
	// equivalent (5 300, computed server-side), rest on the sub-line — never one
	// blended figure and never a float divide.
	const monthly = kpiRow.locator('.kpi').filter({ hasText: /Monthly recurring/i });
	await expect(monthly.locator('.kpi-value')).toContainText('5,300');
	await expect(monthly.locator('.kpi-sub')).toContainText('100');
});
