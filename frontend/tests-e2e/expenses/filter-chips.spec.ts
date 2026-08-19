import { API_BASE, authedTenantHeaders, expect, tenantPsql, test } from '../fixtures/helpers';

/**
 * /expenses status filter chips — only reachable statuses are offered.
 *
 * `ExpenseStatus` has five values, but only three are ever stamped by the
 * backend: `draft` (the column default, and where `reject_report` returns a
 * rejected report's children), `submitted` (`submit_report`) and `approved`
 * (`approve_report`). Nothing writes `rejected` or `reimbursed` — there is no
 * reimbursement transition at all — so those two chips were filters that
 * returned an empty list forever. They are gone from the chip row.
 *
 * The values stay in the type union and the label map, because a row can still
 * ARRIVE carrying one (the demo seed writes both, and a long-lived tenant may
 * hold pre-existing rows). So the page also follows the /invoices
 * `quick subset ∪ active` rule: a status that is actively filtered is appended
 * to the chip row, and its rows render their badge normally. An explicit
 * `?status=reimbursed` must never be an invisible filter.
 */

const CHIPS = '.filter-row .filter-chip';

async function createExpense(
	page: import('@playwright/test').Page,
	merchant: string
): Promise<{ id: string }> {
	const resp = await page.request.post(`${API_BASE}/api/expenses`, {
		headers: await authedTenantHeaders(page),
		data: {
			merchant,
			amount: '31.00',
			currency: 'USD',
			expense_date: '2026-02-02',
			category: 'meals'
		}
	});
	expect(resp.status()).toBe(201);
	return (await resp.json()) as { id: string };
}

function deleteExpense(id: string): void {
	tenantPsql(`DELETE FROM expenses WHERE id='${id}'`);
}

test.describe('/expenses status chips', () => {
	test('offers only the statuses the backend can actually stamp', async ({ page }) => {
		await page.goto('/expenses');
		await expect(page.locator('table')).toBeVisible();

		// Exactly the reachable set, in enum order. `Rejected` / `Reimbursed`
		// are deliberately absent — nothing ever assigns them.
		await expect(page.locator(CHIPS)).toHaveText(['All', 'Draft', 'Submitted', 'Approved']);
	});

	test('an actively filtered unreachable status is still shown as a chip', async ({ page }) => {
		// A deep link / bookmark for a status the chip row no longer offers must
		// not leave the user filtered with no visible control to clear it.
		await page.goto('/expenses?status=reimbursed');
		await expect(page.locator('table')).toBeVisible();

		await expect(page.locator(CHIPS)).toHaveText([
			'All',
			'Draft',
			'Submitted',
			'Approved',
			'Reimbursed'
		]);
		const active = page.locator(`${CHIPS}[aria-pressed="true"]`);
		await expect(active).toHaveText('Reimbursed');

		// Clicking All clears the filter and retires the appended chip.
		await page.locator(CHIPS, { hasText: /^All$/ }).click();
		await expect(page.locator(CHIPS)).toHaveText(['All', 'Draft', 'Submitted', 'Approved']);
		await expect(page).toHaveURL(/\/expenses$/);
	});

	test('a legacy row carrying an unreachable status still renders its badge', async ({ page }) => {
		// The status values stay in the label map precisely for this: a row
		// written before/outside the API must not render a raw enum value.
		const merchant = `E2E Legacy Reimbursed ${Date.now()}`;
		const created = await createExpense(page, merchant);
		try {
			// No API path produces this state — that is the whole point of the
			// chip removal — so the row is aged into it directly, the same way a
			// pre-existing tenant row would already be sitting there.
			tenantPsql(`UPDATE expenses SET status='reimbursed' WHERE id='${created.id}'`);

			await page.goto('/expenses?status=reimbursed');
			const row = page.locator('table tbody tr.clickable', { hasText: merchant });
			await expect(row).toHaveCount(1);
			await expect(row).toContainText('Reimbursed');
		} finally {
			deleteExpense(created.id);
		}
	});
});
