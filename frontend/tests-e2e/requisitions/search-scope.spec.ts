import { expect, test } from '../fixtures/helpers';

/**
 * /requisitions search honesty — the empty state must not claim more than it
 * knows.
 *
 * The term is applied client-side over the rows loaded so far (number / title /
 * department) and deliberately NOT sent server-side:
 * `list_requisitions` ILIKEs `requisition_number` + `title` only, so moving to
 * `?search=` today would silently drop every department-only match the client
 * filter finds — a straight regression. Until the backend grows a `department`
 * leg, the limitation is surfaced rather than hidden.
 *
 * Rendering a flat "No requisitions match your filters." over a partially
 * loaded list is the same dishonest-UI bug as an unconditional "Showing all N"
 * — it asserts something about rows that were never fetched. The empty state
 * now says what was and was not searched, and Load more is still offered; once
 * every row is loaded the claim becomes true and the plain message returns.
 *
 * The list response is stubbed so the assertion doesn't depend on how much the
 * shard's tenant happens to hold.
 */

const ROWS = 'table tbody tr.clickable';

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

// Department is only on page one — it is the field the backend's own `search`
// cannot reach, so it is what proves the client filter is still doing work.
const PAGE_ONE = [1, 2, 3, 4].map((n) => requisition(n, n === 2 ? 'Facilities' : 'Engineering'));
const PAGE_TWO = [5, 6, 7].map((n) => requisition(n, 'Engineering'));
const TOTAL = PAGE_ONE.length + PAGE_TWO.length;

async function stubRequisitionList(page: import('@playwright/test').Page) {
	await page.route('**/api/requisitions?*', async (route) => {
		const url = new URL(route.request().url());
		// The glob also catches nested requisition routes; only the list itself
		// is stubbed.
		if (url.pathname !== '/api/requisitions') {
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

test.describe('/requisitions — client-side search scope', () => {
	test('a no-match search over a partial list says so instead of claiming nothing matched', async ({
		page
	}) => {
		await stubRequisitionList(page);
		await page.goto('/requisitions');
		await expect(page.locator(ROWS)).toHaveCount(PAGE_ONE.length);

		await page.getByPlaceholder('Search requisitions...').fill('zzz-no-such-requisition');

		await expect(page.locator(ROWS)).toHaveCount(0);
		const emptyCell = page.getByTestId('table-empty');
		await expect(emptyCell).toContainText(
			`No match in the ${PAGE_ONE.length} of ${TOTAL} requisitions loaded so far`
		);
		await expect(emptyCell).toContainText('Search covers loaded rows only');

		// Load more is still offered while the term is active — it is the only
		// way to widen a client-side search.
		const loadMore = page.getByRole('button', {
			name: `Load more (${PAGE_ONE.length} of ${TOTAL})`
		});
		await expect(loadMore).toBeVisible();

		await loadMore.click();
		await expect(page.getByRole('button', { name: /Load more/ })).toHaveCount(0);
		await expect(page.getByTestId('table-empty')).toHaveText(
			'No requisitions match your filters.'
		);
	});

	test('a department-only term still matches — the reason search stays client-side', async ({
		page
	}) => {
		// `GET /api/requisitions?search=` searches requisition_number + title
		// only. This match exists ONLY because the filter runs client-side; it is
		// exactly what a premature move to server-side search would break.
		await stubRequisitionList(page);
		await page.goto('/requisitions');
		await expect(page.locator(ROWS)).toHaveCount(PAGE_ONE.length);

		await page.getByPlaceholder('Search requisitions...').fill('Facilities');
		await expect(page.locator(ROWS)).toHaveCount(1);
		await expect(page.locator(ROWS)).toContainText('RQ-STUB-2');
	});
});
