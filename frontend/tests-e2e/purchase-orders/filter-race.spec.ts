import { expect, test } from '../fixtures/helpers';

/**
 * `/purchase-orders` list loading.
 *
 * This was the one list in the app with a debounced search and no
 * `createRequestSequencer`, so a slow response for an earlier filter landed
 * after a faster later one and clobbered the table. Its status chip also shared
 * the text box's 250 ms timer (a discrete click waiting a quarter-second for
 * nothing), and the All chip rendered `total` — the count of whatever filter
 * was active — so picking "Closed" labelled the closed count "All".
 */

function po(n: number, status: string) {
	return {
		id: `00000000-0000-4000-a000-${String(n).padStart(12, '0')}`,
		po_number: `E2E-PO-${n}`,
		vendor_id: null,
		vendor_name: 'E2E Vendor',
		total: 100,
		status,
		line_items: [],
		created_at: '2026-01-01T00:00:00Z'
	};
}

test.describe('/purchase-orders — list request sequencing', () => {
	test('a slow earlier filter response cannot clobber the newer one', async ({ page }) => {
		let releaseSlow: () => void = () => {};
		const slowGate = new Promise<void>((resolve) => (releaseSlow = resolve));

		await page.route('**/api/purchase-orders?*', async (route) => {
			const url = new URL(route.request().url());
			if (url.pathname !== '/api/purchase-orders') {
				await route.continue();
				return;
			}
			const status = url.searchParams.get('status');
			if (status === 'open') {
				// The earlier request: held until the later one has landed.
				await slowGate;
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify({ items: [po(1, 'open')], total: 1 })
				});
				return;
			}
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					items: status === 'closed' ? [po(2, 'closed')] : [po(3, 'open')],
					total: 1
				})
			});
		});

		await page.goto('/purchase-orders');
		await expect(page.getByText('E2E-PO-3')).toBeVisible();

		// Open (slow, held) then Closed (fast) — the chip click must fire the
		// request immediately, not wait behind the search debounce.
		await page.getByRole('button', { name: /^Open/ }).click();
		await page.getByRole('button', { name: /^Closed/ }).click();
		await expect(page.getByText('E2E-PO-2')).toBeVisible();

		// Now let the stale "open" response land. It must be discarded.
		releaseSlow();
		await expect(page.getByText('E2E-PO-2')).toBeVisible();
		await expect(page.getByText('E2E-PO-1')).toHaveCount(0);
	});

	test('the All chip only carries a count while All is the active filter', async ({ page }) => {
		await page.route('**/api/purchase-orders?*', async (route) => {
			const url = new URL(route.request().url());
			if (url.pathname !== '/api/purchase-orders') {
				await route.continue();
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

		await page.goto('/purchase-orders');
		const allChip = page.getByRole('button', { name: /^All/ });
		await expect(allChip).toContainText('2');

		await page.getByRole('button', { name: /^Closed/ }).click();
		await expect(page.getByText('E2E-PO-2')).toBeVisible();
		// The filtered total (1) must not be rendered as the "All" count.
		await expect(allChip).not.toContainText('1');
	});
});
