import { expect, test } from '../fixtures/helpers';

/**
 * /expenses search honesty — the empty state must not claim more than it knows.
 *
 * `GET /api/expenses` has no `search` parameter at all (it takes `status`,
 * `report_id` and pagination), so the merchant/category term is applied
 * client-side over the rows loaded so far. That is a real limitation: a term
 * matching an expense on page 3 finds nothing until the user pages to it.
 *
 * Rendering a flat "No expenses match your filters." over a partially loaded
 * list is the same dishonest-UI bug as an unconditional "Showing all N" — it
 * asserts something about rows that were never fetched. The empty state now
 * says what was and was not searched, and Load more is still offered; once
 * every row is loaded the claim becomes true and the plain message returns.
 *
 * The list response is stubbed so the assertion doesn't depend on how much the
 * shard's tenant happens to hold.
 */

const ROWS = 'table tbody tr.clickable';

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

const PAGE_ONE = [1, 2, 3, 4].map((n) => expense(n, `Stub Merchant ${n}`));
const PAGE_TWO = [5, 6, 7].map((n) => expense(n, `Stub Merchant ${n}`));
const TOTAL = PAGE_ONE.length + PAGE_TWO.length;

async function stubExpenseList(page: import('@playwright/test').Page) {
	await page.route('**/api/expenses?*', async (route) => {
		const url = new URL(route.request().url());
		// The glob also catches /api/expenses/export and /receipt/<key>; only the
		// list itself is stubbed.
		if (url.pathname !== '/api/expenses') {
			await route.continue();
			return;
		}
		const pageNum = Number(url.searchParams.get('page') ?? '1');
		const items = pageNum === 1 ? PAGE_ONE : PAGE_TWO;
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items, total: TOTAL, page: pageNum, page_size: PAGE_ONE.length })
		});
	});
}

test.describe('/expenses — client-side search scope', () => {
	test('a no-match search over a partial list says so instead of claiming nothing matched', async ({
		page
	}) => {
		await stubExpenseList(page);
		await page.goto('/expenses');
		await expect(page.locator(ROWS)).toHaveCount(PAGE_ONE.length);

		await page.getByPlaceholder('Search expenses...').fill('zzz-no-such-merchant');

		// Empty — but honestly empty: it names how much was searched and points
		// at the control that widens the search.
		await expect(page.locator(ROWS)).toHaveCount(0);
		const emptyCell = page.getByTestId('table-empty');
		await expect(emptyCell).toContainText(
			`No match in the ${PAGE_ONE.length} of ${TOTAL} expenses loaded so far`
		);
		await expect(emptyCell).toContainText('Search covers loaded rows only');

		// Load more is still offered while the term is active — it is the only
		// way to widen a client-side search.
		const loadMore = page.getByRole('button', {
			name: `Load more (${PAGE_ONE.length} of ${TOTAL})`
		});
		await expect(loadMore).toBeVisible();

		// Once every row is loaded the flat claim is true again.
		await loadMore.click();
		await expect(page.getByRole('button', { name: /Load more/ })).toHaveCount(0);
		await expect(page.getByTestId('table-empty')).toHaveText('No expenses match your filters.');
	});

	test('a matching term still filters the loaded rows', async ({ page }) => {
		// The client-side filter is the only search this surface has; narrowing
		// the honest-empty-state must not have cost it.
		await stubExpenseList(page);
		await page.goto('/expenses');
		await expect(page.locator(ROWS)).toHaveCount(PAGE_ONE.length);

		await page.getByPlaceholder('Search expenses...').fill('Merchant 2');
		await expect(page.locator(ROWS)).toHaveCount(1);
		await expect(page.locator(ROWS)).toContainText('Stub Merchant 2');
	});
});
