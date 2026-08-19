import { expect, test } from '../fixtures/helpers';

/**
 * `/goods-receipts` — request sequencing.
 *
 * The page runs TWO independent request streams (the list and the detail
 * modal) and neither was sequenced. The reachable failure is the modal: open a
 * receipt, close it, open another, and the first response — still in flight —
 * lands last and fills the open dialog with the OTHER receipt's line items,
 * under a header the second receipt supplied. On a 3-way-match feeder that is
 * the wrong quantities against the right PO.
 *
 * The list stream got the same wiring (`appendUnique` + `canCommit`, and both
 * busy flags cleared only by the newest request — an append used to clear
 * `loading` for a page-1 reload that was still out). It has no filter and its
 * Load-more button is disabled while an append is out, so a stale list response
 * needs a second entry point to provoke; rather than fake one, this spec pins
 * the stream that a user can actually race, and the sequencer wiring is shared.
 */

const GR_A = '00000000-0000-4000-8100-000000000001';
const GR_B = '00000000-0000-4000-8100-000000000002';

function gr(id: string, number: string) {
	return {
		id,
		gr_number: number,
		po_id: null,
		po_number: 'E2E-PO-1',
		received_date: '2026-01-01',
		status: 'received',
		line_count: 1,
		created_at: '2026-01-01T00:00:00Z'
	};
}

function detail(id: string, number: string, description: string) {
	return {
		...gr(id, number),
		line_items: [{ id: `${id}-line`, description, quantity_received: 5 }]
	};
}

test.describe('/goods-receipts — detail request sequencing', () => {
	test('a held detail response cannot land in a modal showing another receipt', async ({
		page
	}) => {
		let releaseFirst: () => void = () => {};
		const firstGate = new Promise<void>((resolve) => (releaseFirst = resolve));

		// A regex, not a glob: Playwright's `*` does not cross a `/`, so
		// `**/api/goods-receipts*` matches the LIST but never
		// `/api/goods-receipts/{id}` — the detail request would fall through to
		// the real backend, 404 on these synthetic ids, and close the modal on
		// its own, so the sequencing this test exists for was never exercised.
		await page.route(/\/api\/goods-receipts(\?|\/|$)/, async (route) => {
			const url = new URL(route.request().url());
			if (url.pathname === '/api/goods-receipts') {
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify({
						items: [gr(GR_A, 'E2E-GR-A'), gr(GR_B, 'E2E-GR-B')],
						total: 2
					})
				});
				return;
			}
			if (url.pathname === `/api/goods-receipts/${GR_A}`) {
				// The first detail read: held past the second open.
				await firstGate;
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify(detail(GR_A, 'E2E-GR-A', 'STALE LINE A'))
				});
				return;
			}
			if (url.pathname === `/api/goods-receipts/${GR_B}`) {
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify(detail(GR_B, 'E2E-GR-B', 'LIVE LINE B'))
				});
				return;
			}
			await route.fallback();
		});

		await page.goto('/goods-receipts');
		const rowA = page.getByRole('row').filter({ hasText: 'E2E-GR-A' });
		const rowB = page.getByRole('row').filter({ hasText: 'E2E-GR-B' });
		await expect(rowA).toBeVisible();

		const modal = page.getByRole('dialog', { name: 'Goods receipt' });

		// Open A — its detail hangs, so the modal sits on its loading state.
		await rowA.getByRole('button', { name: 'View goods receipt E2E-GR-A' }).click();
		await expect(modal).toBeVisible();

		// Close, then open B, which answers immediately.
		await modal.getByRole('button', { name: 'Close' }).click();
		await expect(modal).toBeHidden();
		await rowB.getByRole('button', { name: 'View goods receipt E2E-GR-B' }).click();
		await expect(modal.getByText('LIVE LINE B')).toBeVisible();

		// Release A's response and wait for the page to actually receive it — a
		// real signal, not a sleep.
		const staleResponse = page.waitForResponse(
			(r) => new URL(r.url()).pathname === `/api/goods-receipts/${GR_A}`
		);
		releaseFirst();
		await staleResponse;
		// One animation frame past the response guarantees the fetch continuation
		// (and any state write it would have made) has run.
		await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => r())));

		// A's line items must never appear under B's header.
		await expect(modal.getByText('STALE LINE A')).toHaveCount(0);
		await expect(modal.getByText('LIVE LINE B')).toBeVisible();
		await expect(modal.getByText('E2E-GR-B')).toBeVisible();
	});
});
