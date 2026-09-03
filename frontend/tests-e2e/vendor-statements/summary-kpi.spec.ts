import { expect, test } from '../fixtures/helpers';

/**
 * `/vendor-statements` "Open reconciliations" / "Open discrepancies" KPIs.
 *
 * `openCount` filtered the LOADED page by status and `totalDiscrepancies`
 * reduced the per-run discrepancy counts over it — so both contradicted the
 * "showing all N" footer. They now read `GET /api/vendor-statements/summary` —
 * whole filtered set, `by_status` counts, summed discrepancies, same
 * vendor/status filters as the list.
 */

function recon(n: number, status = 'open') {
	return {
		id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
		vendor_id: null,
		vendor_name: `Vendor ${n}`,
		statement_date: '2026-01-31',
		statement_reference: `ST-${n}`,
		currency: 'USD',
		source_format: 'manual',
		file_key: null,
		has_source_file: false,
		extraction: null,
		status,
		notes: null,
		summary: {
			line_count: 3,
			matched_count: 2,
			amount_mismatch_count: 1,
			missing_our_side_count: 0,
			missing_their_side_count: 0,
			statement_total: null,
			ledger_total: null
		},
		created_at: '2026-02-01T00:00:00Z',
		updated_at: null,
		lines: null
	};
}

test('the reconciliation KPIs are the whole-set rollup, not the loaded page', async ({ page }) => {
	await page.route('**/api/vendor-statements**', async (route) => {
		const url = new URL(route.request().url());
		if (url.pathname === '/api/vendor-statements/summary') {
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					total: 11,
					by_status: { open: 7, resolved: 4 },
					open_discrepancies: 19
				})
			});
		}
		if (url.pathname === '/api/vendor-statements') {
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					items: [recon(1), recon(2, 'resolved')],
					total: 11,
					page: 1,
					page_size: 50
				})
			});
		}
		if (url.pathname === '/api/vendor-statements/close-readiness') {
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ close_ready: true, blocking_count: 0, vendors: [] })
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

	await page.goto('/vendor-statements');

	const kpiRow = page.locator('.kpi-row');
	const open = kpiRow.locator('.kpi', {
		has: page.getByText('Open reconciliations', { exact: true })
	});
	const disc = kpiRow.locator('.kpi', {
		has: page.getByText('Open discrepancies', { exact: true })
	});

	// whole-set by_status (7), not the 1 open row on the loaded page
	await expect(open.locator('.kpi-value')).toHaveText('7');
	// summed across the whole set (19), not the 1 on the loaded open row
	await expect(disc.locator('.kpi-value')).toHaveText('19');
});
