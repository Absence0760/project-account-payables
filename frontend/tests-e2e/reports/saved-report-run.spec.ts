import { expect, test } from '../fixtures/helpers';

/**
 * /reports — running a SAVED report definition.
 *
 * Two regressions, both about `POST /api/reports/{id}/run`:
 *
 *  1. **Edits to a loaded saved report were silently ignored.** The dirty flag
 *     was maintained by an `$effect` that called `specForCompare()` — reading
 *     the very state it snapshotted — so every edit re-fired the effect and
 *     re-pinned the snapshot. `dirtySinceLoad` was permanently `false`, `run()`
 *     always took the "run the PERSISTED spec server-side" branch, and the table
 *     showed the old numbers with no warning. The snapshot is now taken
 *     imperatively in `applyDefinition()` (and cleared in `selectSource()`).
 *
 *  2. **Saved-report pagination was dead.** The client POSTed `{page, page_size}`
 *     in the BODY; the route declares both as `Query(...)`, so they were dropped
 *     and every run came back page 1. They now go in the query string.
 *
 * Fully route-stubbed: the assertions are about WHICH endpoint the client calls
 * and WHAT it puts where, which is exactly the contract that broke. A live
 * backend would answer both requests happily and hide the bug.
 */

const REPORT_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';

const CATALOG = {
	sources: [
		{
			key: 'invoices',
			label: 'Invoices',
			dimensions: [
				{ key: 'vendor_name', label: 'Vendor', type: 'string' },
				{ key: 'status', label: 'Status', type: 'string' }
			],
			measures: [{ key: 'amount', label: 'Amount', aggs: ['sum', 'avg'], type: 'money' }],
			filters: []
		}
	]
};

const DEFINITION = {
	id: REPORT_ID,
	name: 'Vendor Spend',
	description: null,
	data_source: 'invoices',
	dimensions: [{ key: 'vendor_name', grain: null }],
	measures: [{ key: 'amount', agg: 'sum' }],
	filters: [],
	sort: [],
	limit: null,
	created_by_user_id: null,
	created_at: '2026-06-01T00:00:00Z',
	updated_at: '2026-06-20T00:00:00Z'
};

/** A `ReportResult` in the shape `types/reports.ts` declares — `total_rows`
 *  (not `total`) and a `kind` on every column, which is what drives the pager
 *  and the `<Money>` cells. */
function makeResult(rows: Record<string, unknown>[], totalRows: number, page: number) {
	return {
		columns: [
			{ key: 'vendor_name', label: 'Vendor', kind: 'dimension' },
			{ key: 'amount_sum', label: 'Sum of Amount', kind: 'measure', type: 'money' }
		],
		rows,
		total_rows: totalRows,
		page,
		page_size: 100
	};
}

/** Stub the catalog + saved list + the saved definition GET. Returns nothing;
 *  the run endpoints are stubbed per-test so each can assert on them. */
async function stubBuilder(page: import('@playwright/test').Page) {
	await page.route('**/api/reports/catalog', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CATALOG) })
	);
	await page.route(
		(url) => url.pathname === '/api/reports',
		(route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ reports: [DEFINITION] })
			})
	);
	await page.route(
		(url) => url.pathname === `/api/reports/${REPORT_ID}`,
		(route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(DEFINITION)
			})
	);
}

/** Load the stubbed definition into the builder via the saved-reports list's
 *  own "Load report …" control — the path a user takes. Deliberately not the
 *  `?id=` deep-link, so this spec covers the dirty-tracking fix and nothing
 *  else. */
async function loadSavedIntoBuilder(page: import('@playwright/test').Page) {
	await page.goto('/reports');
	const savedSection = page.getByTestId('saved-reports');
	await savedSection.getByRole('button', { name: 'Load report Vendor Spend' }).click();
	await expect(page.getByTestId('loaded-tag')).toBeVisible({ timeout: 10_000 });
}

test.describe('/reports saved-report run (stubbed)', () => {
	test('an unedited loaded report runs the PERSISTED spec server-side', async ({ page }) => {
		await stubBuilder(page);

		let savedRunHits = 0;
		await page.route(`**/api/reports/${REPORT_ID}/run**`, (route) => {
			savedRunHits++;
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(makeResult([{ vendor_name: 'Acme', amount_sum: '100.00' }], 1, 1))
			});
		});
		await page.route('**/api/reports/run', (route) =>
			route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"ad-hoc endpoint should not be called"}' })
		);

		await loadSavedIntoBuilder(page);

		await page.getByTestId('run-report').click();
		await expect(page.getByTestId('report-result')).toBeVisible({ timeout: 10_000 });
		expect(savedRunHits).toBe(1);
	});

	test('EDITING a loaded report makes Run execute the edited spec, not the stale saved one', async ({
		page
	}) => {
		await stubBuilder(page);

		// The saved endpoint answers with the OLD figure; the ad-hoc endpoint with
		// the NEW one. Whichever number reaches the table names the endpoint used.
		await page.route(`**/api/reports/${REPORT_ID}/run**`, (route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(makeResult([{ vendor_name: 'STALE SAVED SPEC', amount_sum: '1.00' }], 1, 1))
			})
		);

		let adhocBody: Record<string, unknown> | null = null;
		await page.route('**/api/reports/run', (route) => {
			adhocBody = JSON.parse(route.request().postData() ?? '{}');
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(makeResult([{ vendor_name: 'LIVE EDITED SPEC', amount_sum: '2.00' }], 1, 1))
			});
		});

		await loadSavedIntoBuilder(page);

		// Edit the loaded spec: add a second group-by dimension.
		await page.getByLabel('Add dimension').selectOption('status');

		await page.getByTestId('run-report').click();
		await expect(page.getByTestId('report-result')).toBeVisible({ timeout: 10_000 });

		// The live edit is what ran.
		await expect(page.getByTestId('report-result')).toContainText('LIVE EDITED SPEC');
		await expect(page.getByTestId('report-result')).not.toContainText('STALE SAVED SPEC');

		// …and the ad-hoc call carried the edited dimension list.
		expect(adhocBody).not.toBeNull();
		const dims = (adhocBody as unknown as { dimensions: { key: string }[] }).dimensions;
		expect(dims.map((d) => d.key)).toEqual(['vendor_name', 'status']);
	});

	test('saved-report pagination sends page/page_size in the QUERY STRING', async ({ page }) => {
		await stubBuilder(page);

		const savedRunUrls: string[] = [];
		await page.route(`**/api/reports/${REPORT_ID}/run**`, (route) => {
			const url = new URL(route.request().url());
			savedRunUrls.push(url.search);
			const requested = Number(url.searchParams.get('page') ?? '1');
			// 250 rows over page_size 100 → three pages, so Next is enabled.
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(
					makeResult([{ vendor_name: `Page ${requested} vendor`, amount_sum: '10.00' }], 250, requested)
				)
			});
		});

		await loadSavedIntoBuilder(page);

		await page.getByTestId('run-report').click();
		await expect(page.getByTestId('report-result')).toContainText('Page 1 vendor', {
			timeout: 10_000
		});

		await page.getByRole('button', { name: 'Next' }).click();
		await expect(page.getByTestId('report-result')).toContainText('Page 2 vendor', {
			timeout: 10_000
		});

		// Sent as query params — a body-only payload was silently dropped by the
		// `Query(...)`-declared route and every run came back page 1.
		expect(savedRunUrls.some((s) => s.includes('page=2'))).toBe(true);
		expect(savedRunUrls.every((s) => s.includes('page_size='))).toBe(true);
	});
});
