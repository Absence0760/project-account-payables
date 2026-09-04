import { expect, test } from '../fixtures/helpers';

/**
 * /expenses search reaches the SERVER.
 *
 * `GET /api/expenses` had no `search` parameter at all, so the merchant term
 * was applied client-side over the rows loaded so far and an expense on page 3
 * read as "nothing matched" until the user paged to it — which the empty state
 * had to admit rather than claim something about rows it never fetched. The
 * endpoint now ILIKEs merchant / description / category, so the whole filtered
 * set is searched and the plain empty message is true again.
 *
 * Two things are asserted, because either alone would pass while the feature
 * was broken:
 *   1. the term rides the query string, and
 *   2. a row that is NOT on the loaded page comes back.
 *
 * Plus the export, which now shares the list's filter builder on both sides of
 * the wire: the CSV button sends the same `status` + `search` the table is
 * showing, and `GET /api/expenses/export` runs them through the backend's own
 * `_expense_list_filters`. And a keystroke must not cost a request.
 * The keystroke test asserts a NEGATIVE over a time window — "no request fired
 * yet" is the one thing that cannot be awaited on a signal — so it uses the
 * same waits (and the same 300ms debounce) as the canonical
 * `tests-e2e/reactivity/search-debounce-race.spec.ts`. Those waits ARE the
 * assertion, not a cushion around a flake.
 *
 * The list response is stubbed so the assertions don't depend on how much the
 * shard's tenant happens to hold.
 */

const ROWS = 'table tbody tr.clickable';
const SEARCH = 'Search expenses...';

function expense(n: number, merchant: string) {
	return {
		id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
		report_id: null,
		expense_date: '2026-03-01',
		merchant,
		category: 'Travel',
		description: null,
		amount: 10 + n,
		currency: 'USD',
		converted_amount: null,
		converted_currency: null,
		converted_fx_rate: null,
		converted_fx_locked_at: null,
		gl_account_id: null,
		receipt_file_key: null,
		receipt_url: null,
		payment_method: 'out_of_pocket',
		card_transaction_id: null,
		policy_violations: null,
		status: 'draft',
		reimbursable: true,
		mileage_miles: null,
		created_at: '2026-03-01T00:00:00Z',
		updated_at: '2026-03-01T00:00:00Z'
	};
}

// The Skyline row sits on page two, so it is unreachable to anything that
// filters the rows already on screen.
const PAGE_ONE = [1, 2, 3, 4].map((n) => expense(n, `Stub Merchant ${n}`));
const PAGE_TWO = [5, 6, 7].map((n) => expense(n, n === 6 ? 'Skyline Hotels' : `Stub Merchant ${n}`));
const ALL = [...PAGE_ONE, ...PAGE_TWO];
const TOTAL = ALL.length;

/** The server's own filter, reproduced: merchant + description + category. */
function matching(term: string) {
	const q = term.trim().toLowerCase();
	if (!q) return null;
	return ALL.filter((e) =>
		[e.merchant, e.description ?? '', e.category ?? ''].some((v) => v.toLowerCase().includes(q))
	);
}

async function stubExpenseEndpoints(page: import('@playwright/test').Page): Promise<{
	listSearches: string[];
	exportUrls: string[];
}> {
	const listSearches: string[] = [];
	const exportUrls: string[] = [];

	// Two routes, not one with a pathname branch: `?` is NOT a glob wildcard in
	// Playwright, so `**/api/expenses?*` matches the LIST only — the export
	// sailed past it to the real backend and the assertion below saw nothing.
	await page.route('**/api/expenses/export*', async (route) => {
		exportUrls.push(route.request().url());
		await route.fulfill({ status: 200, contentType: 'text/csv', body: 'id,merchant\n' });
	});

	await page.route('**/api/expenses?*', async (route) => {
		const url = new URL(route.request().url());
		const term = url.searchParams.get('search') ?? '';
		listSearches.push(term);
		const pageNum = Number(url.searchParams.get('page') ?? '1');
		const hits = matching(term);
		const items = hits !== null ? hits : pageNum === 1 ? PAGE_ONE : PAGE_TWO;
		const total = hits !== null ? hits.length : TOTAL;
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items, total, page: pageNum, page_size: PAGE_ONE.length })
		});
	});
	return { listSearches, exportUrls };
}

test.describe('/expenses — server-side search', () => {
	test('a term matching a row on a later page is found without paging', async ({ page }) => {
		const { listSearches } = await stubExpenseEndpoints(page);
		await page.goto('/expenses');
		await expect(page.locator(ROWS)).toHaveCount(PAGE_ONE.length);
		// The Skyline row is genuinely not among the loaded rows.
		await expect(page.locator(ROWS, { hasText: 'Skyline Hotels' })).toHaveCount(0);

		const searched = page.waitForResponse(
			(r) =>
				new URL(r.url()).pathname === '/api/expenses' &&
				new URL(r.url()).searchParams.get('search') === 'Skyline'
		);
		await page.getByPlaceholder(SEARCH).fill('Skyline');
		await searched;

		await expect(page.locator(ROWS)).toHaveCount(1);
		await expect(page.locator(ROWS, { hasText: 'Skyline Hotels' })).toHaveCount(1);
		// `total` now counts the MATCHES, so nothing is left to load.
		await expect(page.getByRole('button', { name: /Load more/ })).toHaveCount(0);
		await expect(page.getByText('Showing all 1 expense')).toBeVisible();
		expect(listSearches).toContain('Skyline');
	});

	test('a term that matches nothing gets the plain empty state', async ({ page }) => {
		// The transitional "searched only the rows loaded so far" copy is gone —
		// the server searched everything, so the flat claim is now honest.
		await stubExpenseEndpoints(page);
		await page.goto('/expenses');
		await expect(page.locator(ROWS)).toHaveCount(PAGE_ONE.length);

		const searched = page.waitForResponse(
			(r) =>
				new URL(r.url()).pathname === '/api/expenses' &&
				new URL(r.url()).searchParams.get('search') === 'zzz-no-such-merchant'
		);
		await page.getByPlaceholder(SEARCH).fill('zzz-no-such-merchant');
		await searched;

		await expect(page.locator(ROWS)).toHaveCount(0);
		await expect(page.getByTestId('table-empty')).toHaveText('No expenses match your filters.');
	});

	test('the CSV export carries the search term the table is filtered by', async ({ page }) => {
		// `GET /api/expenses/export` now runs `status` / `report_id` / `search`
		// through the same `_expense_list_filters` the list and the KPI rollup
		// use, so "export what I'm looking at" means the rows on screen. Before
		// that leg existed the button deliberately withheld the term (FastAPI
		// drops an undeclared param silently, which would have read as a
		// narrowed CSV that wasn't one) — this asserts the other half of that
		// trade: once the backend can honour it, the term must actually be sent.
		const { exportUrls } = await stubExpenseEndpoints(page);
		await page.goto('/expenses');
		await expect(page.locator(ROWS)).toHaveCount(PAGE_ONE.length);

		const searched = page.waitForResponse(
			(r) =>
				new URL(r.url()).pathname === '/api/expenses' &&
				new URL(r.url()).searchParams.get('search') === 'Skyline'
		);
		await page.getByPlaceholder(SEARCH).fill('Skyline');
		await searched;

		const exported = page.waitForResponse(
			(r) => new URL(r.url()).pathname === '/api/expenses/export'
		);
		await page.getByRole('button', { name: 'Export CSV' }).click();
		await exported;

		expect(exportUrls).toHaveLength(1);
		expect(new URL(exportUrls[0]).searchParams.get('search')).toBe('Skyline');
	});

	test('rapid typing fires one coalesced request, not one per keystroke', async ({ page }) => {
		// The regression this guards: `loadExpenses()` is called synchronously
		// from the statusFilter `$effect`, so an untracked-read slip there makes
		// THAT effect depend on `search` and every keystroke fires its own
		// immediate request — with `appliedSearch` then cancelling the debounce,
		// so the un-debounced one is the only one. A `fill()`-based test cannot
		// see it (one state write, one term); typing can.
		const { listSearches } = await stubExpenseEndpoints(page);
		await page.goto('/expenses');
		await expect(page.locator(ROWS)).toHaveCount(PAGE_ONE.length);

		// Let the mount load settle before measuring.
		await page.waitForTimeout(400);
		listSearches.length = 0;

		// One keystroke at a time, well inside the 300ms debounce window.
		await page.getByPlaceholder(SEARCH).pressSequentially('Skyl', { delay: 30 });

		// Comfortably under 300ms since the last keystroke: nothing fired.
		await page.waitForTimeout(150);
		expect(listSearches, 'no un-debounced request per keystroke').toEqual([]);

		// Past the debounce: exactly one request, for the FINAL term — not one
		// each for "S", "Sk", "Sky", "Skyl".
		await page.waitForTimeout(300);
		expect(listSearches, 'one coalesced request for the final term').toEqual(['Skyl']);
	});
});
