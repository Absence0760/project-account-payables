import { expect, test } from '../fixtures/helpers';

/**
 * `/purchase-orders` — whole-set status tallies on the filter chips.
 *
 * `total` counts the ACTIVE filter's result set, so rendering it on the All
 * chip while another chip was active labelled the filtered count "All". Round
 * 11 stopped the lie by showing that count only while All is itself the active
 * filter — true, but it leaves the page unable to answer the question the chips
 * exist to answer ("how many are open?") on any other filter.
 *
 * The durable fix is the search-aware `/counts` route the other lists already
 * have (`/api/vendors/counts`, `/api/payments/counts`, `/api/invoices/counts`),
 * consumed here. Both halves are pinned: the counts contract when the endpoint
 * answers, and the degradation when it doesn't — no toast, no broken chips, and
 * exactly the pre-counts behaviour.
 */

function po(n: number, status: string) {
	return {
		id: `00000000-0000-4000-a100-${String(n).padStart(12, '0')}`,
		po_number: `E2E-PO-${n}`,
		vendor_id: null,
		vendor_name: 'E2E Vendor',
		total: 100,
		status,
		line_items: [],
		created_at: '2026-01-01T00:00:00Z'
	};
}

/** The paginated list, stubbed per active status filter. */
async function stubList(page: import('@playwright/test').Page) {
	await page.route('**/api/purchase-orders?*', async (route) => {
		const url = new URL(route.request().url());
		if (url.pathname !== '/api/purchase-orders') {
			await route.fallback();
			return;
		}
		const status = url.searchParams.get('status');
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(
				status === 'closed'
					? { items: [po(2, 'closed')], total: 1 }
					: { items: [po(3, 'open'), po(4, 'closed')], total: 2 }
			)
		});
	});
}

test.describe('/purchase-orders — status-chip counts', () => {
	test('the chips carry the whole-set tallies, on every filter', async ({ page }) => {
		await stubList(page);

		const countsRequests: string[] = [];
		// Registered after the list route so it is matched first for its own URL.
		await page.route('**/api/purchase-orders/counts*', async (route) => {
			countsRequests.push(new URL(route.request().url()).search);
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					total: 9,
					by_status: { open: 5, closed: 3, cancelled: 1 }
				})
			});
		});

		await page.goto('/purchase-orders');
		await expect(page.getByText('E2E-PO-3')).toBeVisible();

		const allChip = page.getByRole('button', { name: /^All/ });
		const openChip = page.getByRole('button', { name: /^Open/ });
		const closedChip = page.getByRole('button', { name: /^Closed/ });
		const cancelledChip = page.getByRole('button', { name: /^Cancelled/ });

		// Whole set, not the loaded page and not the filtered total.
		await expect(allChip).toContainText('9');
		await expect(openChip).toContainText('5');
		await expect(closedChip).toContainText('3');
		await expect(cancelledChip).toContainText('1');

		// Switching filter narrows the LIST, never the tallies — that is the whole
		// point of asking the server for them.
		await closedChip.click();
		await expect(page.getByText('E2E-PO-2')).toBeVisible();
		await expect(allChip).toContainText('9');
		await expect(openChip).toContainText('5');

		// The tallies are search-scoped, so a search re-asks with the term.
		countsRequests.length = 0;
		await page.getByPlaceholder('Search PO number...').fill('E2E');
		await expect
			.poll(() => countsRequests.length, { message: 'the search re-fetched the counts' })
			.toBeGreaterThanOrEqual(1);
		expect(countsRequests[countsRequests.length - 1]).toContain('search=E2E');
	});

	test('an unavailable counts endpoint degrades to the pre-counts behaviour', async ({ page }) => {
		await stubList(page);

		let countsRequests = 0;
		await page.route('**/api/purchase-orders/counts*', async (route) => {
			countsRequests += 1;
			await route.fulfill({
				status: 404,
				contentType: 'application/json',
				body: JSON.stringify({ detail: 'Not Found' })
			});
		});

		await page.goto('/purchase-orders');
		await expect(page.getByText('E2E-PO-3')).toBeVisible();

		const allChip = page.getByRole('button', { name: /^All/ });
		const openChip = page.getByRole('button', { name: /^Open/ });

		// Fallback: the All chip carries the filtered total only while All is
		// active, and the per-status chips carry no count at all.
		await expect(allChip).toContainText('2');
		await expect(openChip).toHaveText('Open');

		await page.getByRole('button', { name: /^Closed/ }).click();
		await expect(page.getByText('E2E-PO-2')).toBeVisible();
		// The filtered total (1) must not be rendered as the "All" count.
		await expect(allChip).toHaveText('All');

		// No error toast — a missing tally is not a failure the user can act on.
		await expect(page.locator('.toast')).toHaveCount(0);

		// And the failure latches: a search does not re-issue a request already
		// known to fail.
		const before = countsRequests;
		const searched = page.waitForResponse(
			(r) =>
				new URL(r.url()).pathname === '/api/purchase-orders' &&
				new URL(r.url()).searchParams.get('search') === 'E2E'
		);
		await page.getByPlaceholder('Search PO number...').fill('E2E');
		await searched;
		await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => r())));
		expect(countsRequests).toBe(before);
	});
});
