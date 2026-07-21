import { expect, test } from '../fixtures/helpers';

/**
 * Regression guard for GitHub issue #168.
 *
 * /invoices, /payments (history tab), and /vendors each have a search box
 * that's supposed to debounce at 300ms. The bug: the filter-change `$effect`
 * (status chips / advanced filters) called a params-builder that also read
 * `search` — and Svelte tracks reads transitively through called functions,
 * so that effect ended up depending on `search` too. Every keystroke re-fired
 * it immediately, bypassing the dedicated debounce timer entirely. On top of
 * that, nothing sequenced responses: a slow request for an earlier search
 * term could resolve after a faster later one and clobber the list with
 * stale results.
 *
 * The fix: the params-builder reads `search` via `untrack()` so only the
 * dedicated debounce effect (which reads `search` directly) triggers a
 * fetch, plus a monotonic request-token guard (`$lib/utils/requestSequence.ts`)
 * that discards a response superseded by a later request.
 *
 * Each route below proves both halves of the fix:
 *   (a) rapid typing coalesces into a single debounced request, not one
 *       immediate un-debounced request per keystroke.
 *   (b) a slow response for an earlier search term that resolves AFTER a
 *       faster later one is discarded — the final list stays correct.
 */

interface RouteCase {
	name: string;
	route: string;
	apiPathname: string;
	searchPlaceholder: string;
	/** Extra setup after `page.goto(route)` before the search box is usable
	 *  (e.g. switching to the History tab on /payments). */
	beforeSearch?: (page: import('@playwright/test').Page) => Promise<void>;
	/** Build a fulfill body for a given search term + a unique marker string
	 *  embedded in the one row returned, keyed so the test can assert on it. */
	buildBody: (searchTerm: string, marker: string) => string;
}

const CASES: RouteCase[] = [
	{
		name: 'invoices',
		route: '/invoices',
		apiPathname: '/api/invoices',
		searchPlaceholder: 'Search invoices...',
		buildBody: (searchTerm, marker) =>
			JSON.stringify({
				items: [
					{
						id: `${marker}-id`,
						invoice_number: marker,
						vendor: 'Marker Vendor',
						description: `row for "${searchTerm}"`,
						po_number: 'PO-1',
						amount: '100.00',
						currency: 'USD',
						due_date: '2026-08-01',
						status: 'new',
						warnings: []
					}
				],
				total: 1,
				page: 1,
				page_size: 20
			})
	},
	{
		name: 'vendors',
		route: '/vendors',
		apiPathname: '/api/vendors',
		searchPlaceholder: 'Search vendors...',
		buildBody: (searchTerm, marker) =>
			JSON.stringify({
				items: [
					{
						id: `${marker}-id`,
						name: marker,
						code: null,
						email: null,
						status: 'active',
						source: 'manual',
						invoice_count: 0,
						erp_vendor_id: null,
						bank_details: null,
						screening_status: 'unscreened',
						risk_level: 'unknown',
						payments_blocked: false
					}
				],
				total: 1
			})
	},
	{
		name: 'payments (history tab)',
		route: '/payments',
		apiPathname: '/api/payments',
		searchPlaceholder: 'Search payments...',
		beforeSearch: async (page) => {
			await page.getByRole('button', { name: /History/ }).click();
		},
		buildBody: (searchTerm, marker) =>
			JSON.stringify({
				items: [
					{
						id: `${marker}-id`,
						invoice_number: marker,
						vendor_name: 'Marker Vendor',
						method: 'ach',
						amount: '50.00',
						currency: 'USD',
						status: 'completed',
						reference: `ref-${searchTerm}`,
						created_at: '2026-07-01T00:00:00Z'
					}
				],
				total: 1,
				page: 1,
				page_size: 20
			})
	}
];

for (const c of CASES) {
	test.describe(`${c.name} search — debounce + response sequencing (#168)`, () => {
		test(`${c.name}: rapid typing does not fire one un-debounced request per keystroke`, async ({
			page
		}) => {
			const searchTerms: string[] = [];
			await page.route(`**${c.apiPathname}?*`, async (route) => {
				const url = new URL(route.request().url());
				if (url.pathname !== c.apiPathname) {
					await route.continue();
					return;
				}
				const term = url.searchParams.get('search') ?? '';
				searchTerms.push(term);
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: c.buildBody(term, 'ROW')
				});
			});

			await page.goto(c.route);
			if (c.beforeSearch) await c.beforeSearch(page);

			const search = page.getByPlaceholder(c.searchPlaceholder);
			await expect(search).toBeVisible();

			// Let whatever mount-time fetch(es) fire and settle before measuring.
			await page.waitForTimeout(400);
			searchTerms.length = 0;

			// Type "acme" one keystroke at a time, well inside the 300ms debounce
			// window — a correctly debounced box fires nothing until typing stops.
			await search.pressSequentially('acme', { delay: 30 });

			// Comfortably under 300ms since the last keystroke: no request yet.
			await page.waitForTimeout(150);
			expect(
				searchTerms,
				'no immediate un-debounced request fired per keystroke'
			).toEqual([]);

			// Past the 300ms debounce: exactly one coalesced request, for the
			// FINAL value — not one per keystroke ("a", "ac", "acm", "acme").
			await page.waitForTimeout(300);
			expect(searchTerms, 'exactly one debounced request for the final term').toEqual([
				'acme'
			]);
		});

		test(`${c.name}: a stale slow response cannot clobber a fresher one that resolves first`, async ({
			page
		}) => {
			let releaseStale: () => void = () => {};
			const staleGate = new Promise<void>((resolve) => (releaseStale = resolve));

			await page.route(`**${c.apiPathname}?*`, async (route) => {
				const url = new URL(route.request().url());
				if (url.pathname !== c.apiPathname) {
					await route.continue();
					return;
				}
				const term = url.searchParams.get('search') ?? '';
				if (term === 'acm') {
					// Hold this response open — simulates a slow query for the
					// earlier, superseded search term.
					await staleGate;
					await route.fulfill({
						status: 200,
						contentType: 'application/json',
						body: c.buildBody(term, 'STALE-ACM')
					});
					return;
				}
				if (term === 'acme') {
					await route.fulfill({
						status: 200,
						contentType: 'application/json',
						body: c.buildBody(term, 'FRESH-ACME')
					});
					return;
				}
				await route.continue();
			});

			await page.goto(c.route);
			if (c.beforeSearch) await c.beforeSearch(page);

			const search = page.getByPlaceholder(c.searchPlaceholder);
			await expect(search).toBeVisible();
			await page.waitForTimeout(400); // let the mount-time fetch(es) settle

			// Type "acm" and let its debounced request fire (and hang, gated).
			await search.fill('acm');
			await page.waitForTimeout(350);

			// Now type the rest — "acme" — and let ITS debounced request fire
			// and resolve immediately (faster than the still-pending "acm" one).
			await search.fill('acme');
			await page.waitForTimeout(350);

			await expect(page.getByText('FRESH-ACME')).toBeVisible();

			// Release the slow, now-stale "acm" response. If sequencing is
			// broken, this overwrites the list with the stale row.
			releaseStale();
			await page.waitForTimeout(300);

			await expect(page.getByText('FRESH-ACME')).toBeVisible();
			await expect(page.getByText('STALE-ACM')).toHaveCount(0);
		});
	});
}
