import {
	API_BASE,
	authedTenantHeaders,
	expect,
	tenantPsql,
	test
} from '../fixtures/helpers';

async function createApprovedInvoice(
	page: import('@playwright/test').Page,
	invoiceNumber: string
): Promise<string> {
	const resp = await page.request.post(`${API_BASE}/api/invoices`, {
		headers: await authedTenantHeaders(page),
		data: {
			vendor: 'E2E Pluralization Vendor',
			invoice_number: invoiceNumber,
			amount: 250.0,
			currency: 'USD'
		}
	});
	expect(resp.status()).toBe(201);
	const body = (await resp.json()) as { id: string };
	// POST /api/invoices intentionally ignores a client-supplied status (the
	// status-injection fix — InvoiceCreate has no `status` field). Force the
	// row to `approved` and bind a real vendor_id (required by the compliance
	// gate in execute_payment_run — NULL vendor → pending_compliance) via SQL.
	const vendorId = tenantPsql(
		`SELECT id FROM vendors WHERE status='active' LIMIT 1`
	).trim();
	const sets = `status='approved'${vendorId ? `, vendor_id='${vendorId}'` : ''}`;
	tenantPsql(`UPDATE invoices SET ${sets} WHERE id='${body.id}'`);
	return body.id;
}

/** Wipe an approved test invoice + its workflow rows + audit trail.
 *  The PATCH/DELETE invoice endpoint won't touch approved rows, so
 *  raw SQL is the only revertible path. */
function hardDeleteInvoice(id: string): void {
	tenantPsql(
		`DELETE FROM workflow_steps WHERE instance_id IN (SELECT id FROM workflow_instances WHERE invoice_id='${id}')`
	);
	tenantPsql(`DELETE FROM workflow_instances WHERE invoice_id='${id}'`);
	// audit_log is append-only (DB trigger, migration 0022 + seed) — never DELETE;
	// orphan rows for the removed invoice are harmless (no FK back to invoices).
	tenantPsql(`DELETE FROM invoices WHERE id='${id}'`);
}

/**
 * /payments Queue tab — selection, Review & Pay panel, payment-method
 * selector, and Clear. Stops short of clicking "Create Draft Run" so
 * we don't pollute the seed payment_runs table; that path is exercised
 * by the runs detail tests via the API.
 */

test.describe('/payments queue selection', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/payments');
		// Queue is the default tab, but be defensive in case a prior
		// test left a different tab active in shared state.
		await page.locator('.tab', { hasText: 'Queue' }).click();
	});

	test('queue table renders rows from /api/payments/queue', async ({ page }) => {
		// The seeded `approved` invoices should be ready to pay. If the
		// seed has zero queue items, the rest of the suite is moot, so
		// fail fast with a helpful message.
		const rows = page.locator('table tbody tr');
		await expect(rows.first()).toBeVisible({ timeout: 5_000 });
		expect(await rows.count()).toBeGreaterThan(0);
		// Header includes the checkbox column.
		await expect(page.locator('thead th.checkbox-col input[type="checkbox"]')).toBeVisible();
	});

	test('selecting a row reveals the pay-bar with count + total', async ({ page }) => {
		const firstRow = page.locator('table tbody tr').first();
		await firstRow.locator('input[type="checkbox"]').check();

		const payBar = page.locator('.pay-bar');
		await expect(payBar).toBeVisible();
		await expect(payBar.locator('.pay-bar-count')).toContainText('1 selected');
		// Pay-bar shows a currency-formatted total (USD format includes "$").
		await expect(payBar.locator('.pay-bar-count')).toContainText('$');
	});

	test('selecting all via header checkbox selects every queue row', async ({ page }) => {
		const total = await page.locator('table tbody tr').count();
		await page.locator('thead th.checkbox-col input[type="checkbox"]').check();

		const payBar = page.locator('.pay-bar');
		await expect(payBar).toBeVisible();
		await expect(payBar.locator('.pay-bar-count')).toContainText(`${total} selected`);
		// Every body checkbox is now checked.
		const bodyChecks = page.locator('table tbody tr input[type="checkbox"]');
		const checkCount = await bodyChecks.count();
		for (let i = 0; i < checkCount; i++) {
			await expect(bodyChecks.nth(i)).toBeChecked();
		}
	});

	test('Clear button drops selection and hides pay-bar', async ({ page }) => {
		await page.locator('table tbody tr').first().locator('input[type="checkbox"]').check();
		const payBar = page.locator('.pay-bar');
		await expect(payBar).toBeVisible();

		await payBar.getByRole('button', { name: 'Clear' }).click();
		await expect(payBar).toBeHidden();
		await expect(
			page.locator('table tbody tr').first().locator('input[type="checkbox"]')
		).not.toBeChecked();
	});

	test('Review & Pay opens the panel; method selects default to ACH', async ({ page }) => {
		await page.locator('table tbody tr').first().locator('input[type="checkbox"]').check();
		await page.locator('.pay-bar').getByRole('button', { name: 'Review & Pay' }).click();

		const reviewPanel = page.locator('.review-panel');
		await expect(reviewPanel).toBeVisible();
		await expect(reviewPanel.locator('.review-title')).toContainText('Payment Review');

		// One row per selected invoice; the method <select> defaults to "ach".
		const methodSelect = reviewPanel.locator('select.method-select').first();
		await expect(methodSelect).toBeVisible();
		await expect(methodSelect).toHaveValue('ach');

		// All four options exist (ach, wire, check, virtual_card).
		const optionValues = await methodSelect.locator('option').evaluateAll(
			(opts) => (opts as HTMLOptionElement[]).map((o) => o.value)
		);
		expect(optionValues).toEqual(['ach', 'wire', 'check', 'virtual_card']);
	});

	test('changing the payment-method select sticks while panel is open', async ({ page }) => {
		await page.locator('table tbody tr').first().locator('input[type="checkbox"]').check();
		await page.locator('.pay-bar').getByRole('button', { name: 'Review & Pay' }).click();

		const select = page.locator('.review-panel select.method-select').first();
		await select.selectOption('wire');
		await expect(select).toHaveValue('wire');
		// Switching it again confirms the binding isn't one-way.
		await select.selectOption('check');
		await expect(select).toHaveValue('check');
	});

	test('Review panel button label reflects selection size pluralization', async ({
		page
	}) => {
		// Seed produces only one approved invoice that's still in the
		// queue (the other approved rows already have payment records).
		// Mint two throwaway approved invoices so we can assert "2
		// Invoices" pluralization, then hard-delete via psql.
		const stamp = Date.now();
		const created: string[] = [];
		try {
			created.push(await createApprovedInvoice(page, `E2E-PAY-${stamp}-A`));
			created.push(await createApprovedInvoice(page, `E2E-PAY-${stamp}-B`));

			await page.reload();
			await page.waitForLoadState('networkidle');
			await page.locator('.tab', { hasText: 'Queue' }).click();

			const rowA = page.locator('table tbody tr', { hasText: `E2E-PAY-${stamp}-A` });
			const rowB = page.locator('table tbody tr', { hasText: `E2E-PAY-${stamp}-B` });
			await expect(rowA).toBeVisible();
			await expect(rowB).toBeVisible();

			await rowA.locator('input[type="checkbox"]').check();
			await rowB.locator('input[type="checkbox"]').check();
			await page.locator('.pay-bar').getByRole('button', { name: 'Review & Pay' }).click();

			const executeBtn = page.locator('.review-panel .btn-execute');
			await expect(executeBtn).toContainText('2 Invoices');
		} finally {
			for (const id of created) hardDeleteInvoice(id);
		}
	});
});
