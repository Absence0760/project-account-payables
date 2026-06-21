import { expect, test } from '../fixtures/helpers';

/**
 * Payments History filter-chip counts reflect the whole set, not the loaded page.
 *
 * Regression: the History chips (incl. "All") counted only the loaded
 * (page-1, size-20) payment array, so they undercounted once history
 * paginated. The page now reads tallies from GET /api/payments/counts. We
 * mock that at 30 completed while the list page returns 2 rows, and assert
 * the All chip shows 30.
 */
test.describe('payments history chip counts', () => {
	test('All chip reflects the counts endpoint, not the loaded page', async ({ page }) => {
		await page.route('**/api/payments/counts**', (r) =>
			r.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ total: 30, by_status: { completed: 30 } })
			})
		);
		// History list returns only two rows — far fewer than 30.
		await page.route(/\/api\/payments\?/, (r) =>
			r.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					items: [
						{ id: '00000000-0000-0000-0000-0000000000c1', invoice_id: null, amount: '10.00', currency: 'USD', method: 'ach', status: 'completed', reference: null, created_at: '2026-06-01T00:00:00Z' },
						{ id: '00000000-0000-0000-0000-0000000000c2', invoice_id: null, amount: '10.00', currency: 'USD', method: 'ach', status: 'completed', reference: null, created_at: '2026-06-01T00:00:00Z' }
					],
					total: 30,
					page: 1,
					page_size: 20
				})
			})
		);

		await page.goto('/payments');
		// Switch to the History tab (chips only render there).
		await page.getByRole('button', { name: 'History', exact: true }).click();

		// The "All" chip carries the whole-set count (30), not the 2 loaded.
		const allChip = page.locator('.filter-chip', { hasText: 'All' });
		await expect(allChip).toBeVisible();
		await expect(allChip).toContainText('30');
	});
});
