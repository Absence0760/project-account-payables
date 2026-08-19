import { expect, test } from '../fixtures/helpers';

/**
 * /requisitions search reaches the SERVER.
 *
 * The term used to be applied client-side over the rows loaded so far, so a
 * requisition matching on page 2 read as "nothing matched" until the user
 * paged to it — the page had to say so in its empty state rather than claim
 * something about rows it never fetched. `GET /api/requisitions?search=` now
 * ILIKEs `requisition_number` + `title` + `department` (the three columns this
 * table renders), so the whole filtered set is searched and the plain empty
 * message is true again.
 *
 * Two things are asserted, because either alone would pass while the feature
 * was broken:
 *   1. the term rides the query string, and
 *   2. a row that is NOT on the loaded page comes back.
 *
 * Plus the two hazards the server round-trip introduces: a slow response for
 * an earlier term must never land on top of a faster later one
 * (`createRequestSequencer`, frontend/CLAUDE.md § Sequencing list fetches), and
 * a keystroke must not cost a request.
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
const SEARCH = 'Search requisitions...';

function requisition(n: number, department: string) {
	return {
		id: `00000000-0000-4000-9000-${String(n).padStart(12, '0')}`,
		requisition_number: `RQ-STUB-${n}`,
		title: `Stub requisition ${n}`,
		requester_user_id: '00000000-0000-4000-9999-000000000001',
		department,
		status: 'draft',
		needed_by: null,
		justification: null,
		vendor_id: null,
		contract_id: null,
		budget_id: null,
		total: 100 + n,
		currency: 'USD',
		notes: null,
		submitted_at: null,
		approved_at: null,
		approved_by: null,
		rejection_reason: null,
		converted_po_id: null,
		line_items: [],
		created_at: '2026-03-01T00:00:00Z',
		updated_at: '2026-03-01T00:00:00Z'
	};
}

// Page one is Engineering; the ONLY Facilities row sits on page two, so it is
// unreachable to anything that filters the rows already on screen.
const PAGE_ONE = [1, 2, 3, 4].map((n) => requisition(n, 'Engineering'));
const PAGE_TWO = [5, 6, 7].map((n) => requisition(n, n === 6 ? 'Facilities' : 'Engineering'));
const ALL = [...PAGE_ONE, ...PAGE_TWO];
const TOTAL = ALL.length;

/** The server's own filter, reproduced: number + title + department, ILIKE. */
function matching(term: string) {
	const q = term.trim().toLowerCase();
	if (!q) return null;
	return ALL.filter((r) =>
		[r.requisition_number, r.title, r.department].some((v) => v.toLowerCase().includes(q))
	);
}

interface StubOptions {
	/** Terms whose response is held open until `release()` is called. */
	hold?: string;
	onHeldIssued?: () => void;
	gate?: Promise<void>;
}

async function stubRequisitionList(
	page: import('@playwright/test').Page,
	opts: StubOptions = {}
): Promise<string[]> {
	const seenSearches: string[] = [];
	await page.route('**/api/requisitions?*', async (route) => {
		const url = new URL(route.request().url());
		// The glob also catches nested requisition routes; only the list itself
		// is stubbed.
		if (url.pathname !== '/api/requisitions') {
			await route.continue();
			return;
		}
		const term = url.searchParams.get('search') ?? '';
		seenSearches.push(term);
		const pageNum = Number(url.searchParams.get('page') ?? '1');
		const hits = matching(term);
		const items = hits !== null ? hits : pageNum === 1 ? PAGE_ONE : PAGE_TWO;
		const total = hits !== null ? hits.length : TOTAL;

		if (opts.hold && term === opts.hold) {
			opts.onHeldIssued?.();
			await opts.gate;
		}

		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items, total, page: pageNum, page_size: PAGE_ONE.length })
		});
	});
	return seenSearches;
}

test.describe('/requisitions — server-side search', () => {
	test('a term matching a row on a later page is found without paging', async ({ page }) => {
		const seenSearches = await stubRequisitionList(page);
		await page.goto('/requisitions');
		await expect(page.locator(ROWS)).toHaveCount(PAGE_ONE.length);
		// The Facilities row is genuinely not among the loaded rows.
		await expect(page.locator(ROWS, { hasText: 'RQ-STUB-6' })).toHaveCount(0);

		const searched = page.waitForResponse(
			(r) =>
				new URL(r.url()).pathname === '/api/requisitions' &&
				new URL(r.url()).searchParams.get('search') === 'Facilities'
		);
		await page.getByPlaceholder(SEARCH).fill('Facilities');
		await searched;

		// One row — the one that was never on screen.
		await expect(page.locator(ROWS, { hasText: 'RQ-STUB-6' })).toHaveCount(1);
		await expect(page.locator(ROWS)).toHaveCount(1);
		// `total` now counts the MATCHES, so the footer is a true statement and
		// there is nothing left to load.
		await expect(page.getByRole('button', { name: /Load more/ })).toHaveCount(0);
		await expect(page.getByText('Showing all 1 requisition')).toBeVisible();
		expect(seenSearches).toContain('Facilities');
	});

	test('a term that matches nothing gets the plain empty state', async ({ page }) => {
		// The transitional "searched only the rows loaded so far" copy is gone —
		// the server searched everything, so the flat claim is now honest even
		// while more pages of the UNFILTERED list would have existed.
		await stubRequisitionList(page);
		await page.goto('/requisitions');
		await expect(page.locator(ROWS)).toHaveCount(PAGE_ONE.length);

		const searched = page.waitForResponse(
			(r) =>
				new URL(r.url()).pathname === '/api/requisitions' &&
				new URL(r.url()).searchParams.get('search') === 'zzz-no-such-requisition'
		);
		await page.getByPlaceholder(SEARCH).fill('zzz-no-such-requisition');
		await searched;

		await expect(page.locator(ROWS)).toHaveCount(0);
		await expect(page.getByTestId('table-empty')).toHaveText(
			'No requisitions match your filters.'
		);
	});

	test('a slow response for an earlier term cannot clobber a later one', async ({ page }) => {
		// Now that the term costs a request, "acm resolves after acme" is
		// reachable on this page. Hold the FIRST term's response open until the
		// second has already rendered, then let it land.
		let releaseSlow!: () => void;
		const gate = new Promise<void>((resolve) => {
			releaseSlow = resolve;
		});
		let slowIssuedResolve!: () => void;
		const slowIssued = new Promise<void>((resolve) => {
			slowIssuedResolve = resolve;
		});

		await stubRequisitionList(page, {
			hold: 'Engineering',
			onHeldIssued: () => slowIssuedResolve(),
			gate
		});

		await page.goto('/requisitions');
		await expect(page.locator(ROWS)).toHaveCount(PAGE_ONE.length);

		// Wait on the request actually reaching the stub — a real signal, not a
		// guess about when the 300ms debounce fired.
		await page.getByPlaceholder(SEARCH).fill('Engineering');
		await slowIssued;

		const secondLanded = page.waitForResponse(
			(r) =>
				new URL(r.url()).pathname === '/api/requisitions' &&
				new URL(r.url()).searchParams.get('search') === 'Facilities'
		);
		await page.getByPlaceholder(SEARCH).fill('Facilities');
		await secondLanded;
		await expect(page.locator(ROWS)).toHaveCount(1);

		// Release the stale response and wait for it to actually be delivered.
		const staleLanded = page.waitForResponse(
			(r) =>
				new URL(r.url()).pathname === '/api/requisitions' &&
				new URL(r.url()).searchParams.get('search') === 'Engineering'
		);
		releaseSlow();
		await staleLanded;

		// Still the newer term's result — the 6 Engineering rows never appear.
		await expect(page.locator(ROWS)).toHaveCount(1);
		await expect(page.locator(ROWS, { hasText: 'RQ-STUB-6' })).toHaveCount(1);
	});

	test('rapid typing fires one coalesced request, not one per keystroke', async ({ page }) => {
		// The regression this guards: `load()` is called synchronously from the
		// statusFilter `$effect`, so an untracked-read slip there makes THAT
		// effect depend on `search` and every keystroke fires its own immediate
		// request — with `appliedSearch` then cancelling the debounce, so the
		// un-debounced one is the only one. A `fill()`-based test cannot see it
		// (one state write, one term); typing can.
		const seenSearches = await stubRequisitionList(page);
		await page.goto('/requisitions');
		await expect(page.locator(ROWS)).toHaveCount(PAGE_ONE.length);

		// Let the mount load settle before measuring.
		await page.waitForTimeout(400);
		seenSearches.length = 0;

		// One keystroke at a time, well inside the 300ms debounce window.
		await page.getByPlaceholder(SEARCH).pressSequentially('Facil', { delay: 30 });

		// Comfortably under 300ms since the last keystroke: nothing fired.
		await page.waitForTimeout(150);
		expect(seenSearches, 'no un-debounced request per keystroke').toEqual([]);

		// Past the debounce: exactly one request, for the FINAL term — not one
		// each for "F", "Fa", "Fac", "Faci", "Facil".
		await page.waitForTimeout(300);
		expect(seenSearches, 'one coalesced request for the final term').toEqual(['Facil']);
	});
});
