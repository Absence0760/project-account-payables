import { expect, test } from '../fixtures/helpers';

/**
 * `/budgets` paged list footer.
 *
 * The page asked the API for one capped page and then rendered
 * "Showing all {total}" unconditionally — where `total` is the server's count
 * of the *whole* filtered set. A tenant with more budgets than the page size
 * saw a truncated table under a footer asserting nothing was missing, with no
 * control to reach the rest. The footer now only makes that claim once every
 * row is loaded; until then it offers Load more.
 *
 * The list response is stubbed so the assertion doesn't depend on how much the
 * shard's tenant happens to hold.
 */

function budget(n: number) {
	return {
		id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
		name: `E2E Budget ${n}`,
		dimension: 'department',
		dimension_value: `Dept ${n}`,
		period: '2026',
		period_start: '2026-01-01',
		period_end: '2026-12-31',
		amount: 1000,
		currency: 'USD',
		notes: null,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z'
	};
}

test.describe('/budgets — Load more', () => {
	test('a capped page offers Load more and only claims "Showing all" at the end', async ({
		page
	}) => {
		const TOTAL = 7;
		await page.route('**/api/budgets?*', async (route) => {
			const url = new URL(route.request().url());
			if (url.pathname !== '/api/budgets') {
				await route.continue();
				return;
			}
			const pageNum = Number(url.searchParams.get('page') ?? '1');
			const items = pageNum === 1 ? [1, 2, 3, 4].map(budget) : [5, 6, 7].map(budget);
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ items, total: TOTAL, page: pageNum, page_size: 4 })
			});
		});

		await page.goto('/budgets');

		// Page one: four of seven rows, so the footer must not claim otherwise.
		await expect(page.locator('table tbody tr')).toHaveCount(4);
		await expect(page.getByText(`Showing all ${TOTAL}`)).toHaveCount(0);

		const loadMore = page.getByRole('button', { name: `Load more (4 of ${TOTAL})` });
		await expect(loadMore).toBeVisible();

		// Page two appends rather than replaces, and the claim becomes true.
		await loadMore.click();
		await expect(page.locator('table tbody tr')).toHaveCount(TOTAL);
		await expect(page.getByText('E2E Budget 1')).toBeVisible();
		await expect(page.getByText('E2E Budget 7')).toBeVisible();
		await expect(page.getByRole('button', { name: /Load more/ })).toHaveCount(0);
		await expect(page.getByText(`Showing all ${TOTAL} budgets`)).toBeVisible();
	});
});
