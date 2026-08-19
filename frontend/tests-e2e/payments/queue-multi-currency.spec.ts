import {
	API_BASE,
	authedTenantHeaders,
	expect,
	tenantPsql,
	test
} from '../fixtures/helpers';

/**
 * /payments Queue tab — the pay-bar must never sum across currencies.
 *
 * `docs/followups.md` item 13: `selectedTotal` / `selectedSavings` reduced
 * every selected row into ONE figure rendered in the org default currency,
 * while each row carries its own `currency` — so a EUR 100 + USD 100
 * selection read as a single meaningless number.
 *
 * The house rule (the same one `/cfo` applies with `unconverted_count` and
 * `/discounts` with `unconvertible_count`) is: be honest about what could not
 * be combined rather than render a wrong single number. No FX conversion is
 * invented on a read.
 *
 * There is a second, harder reason this matters:
 * `services/payment_runs.create_payment_run_for_invoices` **422s** a run
 * spanning more than one currency ("All invoices in a payment run must share
 * the same currency"), because `PaymentRun.total_amount` is one bare Numeric
 * with no currency of its own. So a mixed selection isn't merely unreadable —
 * it cannot be submitted at all, and the page says so before the request.
 */

async function createApprovedInvoice(
	page: import('@playwright/test').Page,
	invoiceNumber: string,
	currency: string,
	amount = 100.0
): Promise<string> {
	const resp = await page.request.post(`${API_BASE}/api/invoices`, {
		headers: await authedTenantHeaders(page),
		data: {
			vendor: 'E2E Multi-Currency Vendor',
			invoice_number: invoiceNumber,
			amount,
			currency
		}
	});
	expect(resp.status()).toBe(201);
	const body = (await resp.json()) as { id: string };
	const vendorId = tenantPsql(`SELECT id FROM vendors WHERE status='active' LIMIT 1`).trim();
	const sets = `status='approved'${vendorId ? `, vendor_id='${vendorId}'` : ''}`;
	tenantPsql(`UPDATE invoices SET ${sets} WHERE id='${body.id}'`);
	return body.id;
}

function hardDeleteInvoice(id: string): void {
	tenantPsql(
		`DELETE FROM workflow_steps WHERE instance_id IN (SELECT id FROM workflow_instances WHERE invoice_id='${id}')`
	);
	tenantPsql(`DELETE FROM workflow_instances WHERE invoice_id='${id}'`);
	tenantPsql(`DELETE FROM exceptions WHERE invoice_id='${id}'`);
	tenantPsql(`DELETE FROM invoices WHERE id='${id}'`);
}

test.describe('/payments queue — mixed-currency selection', () => {
	test('shows a per-currency subtotal, never one summed figure', async ({ page }) => {
		const stamp = Date.now();
		const usdNumber = `E2E-FX-${stamp}-USD`;
		const eurNumber = `E2E-FX-${stamp}-EUR`;
		const created: string[] = [];
		try {
			created.push(await createApprovedInvoice(page, usdNumber, 'USD'));
			created.push(await createApprovedInvoice(page, eurNumber, 'EUR'));

			await page.goto('/payments');
			await page.locator('.tab', { hasText: 'Queue' }).click();

			const usdRow = page.locator('table tbody tr', { hasText: usdNumber });
			const eurRow = page.locator('table tbody tr', { hasText: eurNumber });
			await expect(usdRow).toBeVisible();
			await expect(eurRow).toBeVisible();

			// One currency: the bar reads exactly as it always did, but in the
			// ROW's own currency rather than the org default.
			await usdRow.locator('input[type="checkbox"]').check();
			const count = page.getByTestId('pay-bar-count');
			await expect(count).toContainText('1 selected');
			await expect(count).toContainText('$100.00');
			await expect(page.getByTestId('mixed-currency-warning')).toHaveCount(0);
			await expect(
				page.locator('.pay-bar').getByRole('button', { name: 'Review & Pay' })
			).toBeEnabled();

			// Add a second currency: two subtotals side by side, and NOT "$200.00".
			await eurRow.locator('input[type="checkbox"]').check();
			await expect(count).toContainText('2 selected');
			await expect(count).toContainText('$100.00');
			await expect(count).toContainText('€100.00');
			await expect(count).not.toContainText('$200.00');
			await expect(count).not.toContainText('€200.00');

			// And the page refuses the run up front, because the backend would.
			await expect(page.getByTestId('mixed-currency-warning')).toBeVisible();
			await expect(
				page.locator('.pay-bar').getByRole('button', { name: 'Review & Pay' })
			).toBeDisabled();

			// Narrowing back to one currency clears both.
			await eurRow.locator('input[type="checkbox"]').uncheck();
			await expect(page.getByTestId('mixed-currency-warning')).toHaveCount(0);
			await expect(count).toContainText('$100.00');
			await expect(
				page.locator('.pay-bar').getByRole('button', { name: 'Review & Pay' })
			).toBeEnabled();
		} finally {
			for (const id of created) hardDeleteInvoice(id);
		}
	});

	test('a same-currency selection still totals exactly (no regression)', async ({ page }) => {
		// The single-currency path is the one the old `sumMoney` reduce got
		// right; grouping must not break it. Two $100 invoices = $200.00.
		const stamp = Date.now();
		const a = `E2E-FXSUM-${stamp}-A`;
		const b = `E2E-FXSUM-${stamp}-B`;
		const created: string[] = [];
		try {
			created.push(await createApprovedInvoice(page, a, 'USD'));
			created.push(await createApprovedInvoice(page, b, 'USD'));

			await page.goto('/payments');
			await page.locator('.tab', { hasText: 'Queue' }).click();

			await page
				.locator('table tbody tr', { hasText: a })
				.locator('input[type="checkbox"]')
				.check();
			await page
				.locator('table tbody tr', { hasText: b })
				.locator('input[type="checkbox"]')
				.check();

			const count = page.getByTestId('pay-bar-count');
			await expect(count).toContainText('2 selected');
			await expect(count).toContainText('$200.00');
			await expect(page.getByTestId('mixed-currency-warning')).toHaveCount(0);
		} finally {
			for (const id of created) hardDeleteInvoice(id);
		}
	});
});
