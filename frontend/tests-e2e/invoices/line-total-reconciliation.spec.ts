import { expect, test } from '../fixtures/helpers';

/**
 * Line-item ↔ header reconciliation, surfaced inline in the invoice modal.
 *
 * `PUT /api/invoices/{id}/line-items` reports `reconciles_with_header` (plus
 * both exact-decimal figures) and, on a divergence, raises an `error`-severity
 * `line_total_mismatch` warning + a queued Exception. The modal used to
 * discard that response, so the person who caused the divergence saw nothing
 * until they reopened the invoice — while the mismatch silently blocked the
 * invoice from entering a payment run (`PAYMENT_BLOCKING_EXCEPTION_TYPES`).
 *
 * These tests drive the REAL endpoint (no response mocking): a hand-keyed
 * invoice with a known header amount gets a line item that deliberately
 * disagrees, then one that agrees. Every wait is on a real signal — the PUT
 * response, or the panel appearing/disappearing.
 *
 * See `backend/docs/line-total-reconciliation.md`.
 */

/** Create a manual invoice with a known header amount and open its modal. */
async function createAndOpenInvoice(
	page: import('@playwright/test').Page,
	amount: string
): Promise<string> {
	const invoiceNumber = `E2E-LTR-${Date.now()}`;

	await page.goto('/invoices');
	await page.getByRole('button', { name: 'Create Invoice' }).click();

	const createModal = page.locator('div.modal[role="dialog"][aria-label="Create Invoice"]');
	await expect(createModal).toBeVisible();
	await createModal.locator('label', { hasText: 'Vendor' }).locator('input').fill('E2E LTR Vendor');
	await createModal.locator('label', { hasText: 'Invoice #' }).locator('input').fill(invoiceNumber);
	await createModal.locator('label', { hasText: 'Amount' }).locator('input').fill(amount);
	await createModal.getByRole('button', { name: 'Create' }).click();
	await expect(createModal).not.toBeVisible({ timeout: 10_000 });

	const row = page.locator('table tbody tr', { hasText: invoiceNumber });
	await expect(row).toBeVisible({ timeout: 10_000 });
	await row.getByRole('button', { name: 'Edit' }).click();

	await expect(page.locator('div.modal[role="dialog"][aria-label*="Edit invoice"]')).toBeVisible();
	return invoiceNumber;
}

/** Save the line items and resolve once the backend has answered. */
async function saveLines(page: import('@playwright/test').Page) {
	const modal = page.locator('div.modal[role="dialog"][aria-label*="Edit invoice"]');
	const responsePromise = page.waitForResponse(
		(r) => r.url().includes('/line-items') && r.request().method() === 'PUT'
	);
	await modal.getByRole('button', { name: 'Save Line Items' }).click();
	const response = await responsePromise;
	expect(response.ok()).toBeTruthy();
	return response;
}

test.describe('/invoices — line-total reconciliation', () => {
	test('a diverging save surfaces the mismatch inline, with both figures and the payment-run consequence', async ({
		page
	}) => {
		await createAndOpenInvoice(page, '100.00');
		const modal = page.locator('div.modal[role="dialog"][aria-label*="Edit invoice"]');

		// No mismatch is claimed before anything is saved.
		await expect(modal.locator('[data-testid="line-total-mismatch"]')).toHaveCount(0);

		await modal.getByRole('button', { name: '+ Add Line' }).click();
		await modal.getByLabel('Line 1 description').fill('Deliberately wrong line');
		await modal.getByLabel('Line 1 total').fill('999.99');

		const response = await saveLines(page);
		// The backend is the source of truth for the verdict — assert it agrees
		// with what the UI is about to claim.
		expect(await response.json()).toMatchObject({ reconciles_with_header: false });

		const panel = modal.locator('[data-testid="line-total-mismatch"]');
		await expect(panel).toBeVisible();
		// Announced, not merely coloured.
		await expect(panel).toHaveAttribute('role', 'alert');
		// Both sides of the disagreement are named and shown as money.
		await expect(panel).toContainText('Line items total');
		await expect(panel).toContainText('$999.99');
		await expect(panel).toContainText('Invoice amount');
		await expect(panel).toContainText('$100.00');
		// The money consequence is spelled out, not left implicit.
		await expect(panel).toContainText('cannot enter a payment run');
	});

	test('correcting the lines back into agreement clears the inline mismatch', async ({ page }) => {
		await createAndOpenInvoice(page, '100.00');
		const modal = page.locator('div.modal[role="dialog"][aria-label*="Edit invoice"]');
		const panel = modal.locator('[data-testid="line-total-mismatch"]');

		await modal.getByRole('button', { name: '+ Add Line' }).click();
		await modal.getByLabel('Line 1 description').fill('Deliberately wrong line');
		await modal.getByLabel('Line 1 total').fill('999.99');
		await saveLines(page);
		await expect(panel).toBeVisible();

		// Correct the line so the sum matches the header.
		await modal.getByLabel('Line 1 total').fill('100.00');
		const response = await saveLines(page);
		expect(await response.json()).toMatchObject({ reconciles_with_header: true });

		// A stuck flag would be as useless as no flag — the panel must retire.
		await expect(panel).toHaveCount(0);
	});
});
