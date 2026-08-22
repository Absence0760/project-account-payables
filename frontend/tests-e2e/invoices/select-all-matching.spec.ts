import { deleteInvoicesWhere, expect, tenantPsql, test } from '../fixtures/helpers';

/**
 * /invoices — "select all N matching" covers the WHOLE filtered set, not
 * just the loaded page.
 *
 * Regression for the persona-panel finding (issue #328, power-user/High):
 * the header checkbox only ever selected `invoiceStore.all` — the rows
 * fetched so far (page_size 20) — so a bulk action over "select all"
 * silently skipped every matching row past the first page, with no warning.
 * The fix adds a "Select all N matching" affordance that resolves the whole
 * filtered set via `GET /api/invoices/ids` before selecting.
 *
 * This seeds 25 invoices (more than one page), narrows the list to exactly
 * that set via search, selects the loaded page, then uses "select all
 * matching" and bulk-deletes. The proof this test pins: ALL 25 rows are
 * gone afterward — not just the 20 that were ever rendered in the DOM.
 */

const MARKER = 'SELALL-INV-';

function seedInvoices(n: number): void {
	tenantPsql(
		`INSERT INTO invoices (id, correlation_id, organization_id, invoice_number, vendor_name, amount, currency, status, created_at, updated_at)
		 SELECT gen_random_uuid(), gen_random_uuid(), (SELECT organization_id FROM invoices LIMIT 1),
		        '${MARKER}' || g, 'SelAll Vendor', 100.00, 'USD', 'new', now(), now()
		 FROM generate_series(1, ${n}) g`
	);
}

function countRemaining(): number {
	return Number(
		tenantPsql(`SELECT count(*) FROM invoices WHERE invoice_number LIKE '${MARKER}%'`).trim()
	);
}

test.describe('/invoices "select all N matching" (acme admin)', () => {
	test.afterEach(() => deleteInvoicesWhere(`invoice_number LIKE '${MARKER}%'`));

	test('bulk delete via "select all matching" reaches every row past the loaded page', async ({
		page
	}) => {
		seedInvoices(25);

		await page.goto('/invoices');
		await expect(page.locator('table tbody tr').first()).toBeVisible();

		const searchResponse = page.waitForResponse(
			(r) =>
				r.url().includes('/api/invoices?') &&
				r.url().includes(`search=${encodeURIComponent(MARKER)}`) &&
				r.request().method() === 'GET'
		);
		await page.getByPlaceholder('Search invoices...').fill(MARKER);
		await searchResponse;

		// Only one page's worth loads, even though 25 rows match the filter.
		const rows = page.locator('table tbody tr');
		await expect(rows).toHaveCount(20);

		// Select the loaded page via the header checkbox.
		await page.locator('th.checkbox-col input[type="checkbox"]').check();
		await expect(page.locator('.bulk-count')).toContainText('20 selected');

		// More rows match (25) than are loaded (20): the "select all N
		// matching" affordance must be offered.
		const selectAllBtn = page.locator('.bulk-select-all-matching');
		await expect(selectAllBtn).toBeVisible();
		await expect(selectAllBtn).toContainText('25');

		const idsResponse = page.waitForResponse(
			(r) => r.url().includes('/api/invoices/ids') && r.request().method() === 'GET'
		);
		await selectAllBtn.click();
		await idsResponse;

		// The whole filtered set is now selected — not just the loaded page.
		await expect(page.locator('.bulk-count')).toContainText('25 selected');
		await expect(page.locator('.bulk-select-all-matching')).toHaveCount(0);
		await expect(page.locator('.bulk-all-matching-note')).toBeVisible();

		// Bulk-delete the whole matching set (two-click confirm).
		const bar = page.locator('.bulk-bar');
		await bar.getByRole('button', { name: 'Delete' }).click();
		const deleteResponse = page.waitForResponse(
			(r) => r.url().includes('/api/invoices/bulk/delete') && r.request().method() === 'POST'
		);
		await bar.getByRole('button', { name: 'Confirm Delete' }).click();
		await deleteResponse;

		// The core regression check: ALL 25 seeded rows are gone, not just the
		// 20 that were ever rendered into the DOM. A pre-fix "select all"
		// would have deleted only 20 and left 5 behind with no warning.
		await expect.poll(() => countRemaining()).toBe(0);
	});
});
