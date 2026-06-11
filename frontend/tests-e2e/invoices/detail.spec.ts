import { expect, test } from '../fixtures/helpers';

/**
 * Invoice detail modal — opens when the row's invoice-number link
 * ("Edit invoice …" RowLink) is clicked. The modal renders three
 * things we care about end-to-end:
 *   1. A heading with the invoice number ("Edit Invoice — INV-2024-…")
 *   2. The Line Items section (or its empty placeholder)
 *   3. The Activity / audit-log section (visible because seed creates
 *      audit entries during invoice creation + status transitions)
 *
 * The PDF preview pane is best-effort — seeded invoices may or may not
 * have an attached file_url. Those assertions go in a separate spec
 * once we seed an invoice with a known PDF.
 */

test.describe('/invoices invoice detail modal', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/invoices');
		// Wait for the table to actually populate before opening a row.
		await expect(page.locator('table tbody tr').first()).toBeVisible();
	});

	test('Edit opens the modal with the invoice number in the heading', async ({
		page
	}) => {
		// Read the invoice number from the first row's "Invoice #"
		// column. Columns: 0 = checkbox, 1 = invoice number, 2 = vendor,
		// ..., last = actions.
		const firstInvoiceNumber = (
			await page.locator('table tbody tr').first().locator('td').nth(1).textContent()
		)?.trim();
		expect(firstInvoiceNumber).toBeTruthy();

		await page.locator('table tbody tr').first().getByRole('button', { name: 'Edit' }).click();

		const modal = page.locator('div.modal[role="dialog"][aria-label*="Edit invoice"]');
		await expect(modal).toBeVisible();
		await expect(modal.locator('header h2')).toContainText(firstInvoiceNumber!);
	});

	test('modal shows the Line Items section', async ({ page }) => {
		await page.locator('table tbody tr').first().getByRole('button', { name: 'Edit' }).click();

		const modal = page.locator('div.modal[role="dialog"]');
		await expect(modal).toBeVisible();

		// The line-items-section is always rendered; its content is
		// either a table or a "no line items" placeholder. The section
		// title "Line Items" is the stable contract.
		await expect(modal.locator('.line-items-title')).toHaveText('Line Items');
	});

	test('modal shows the Activity audit timeline once loaded', async ({ page }) => {
		await page.locator('table tbody tr').first().getByRole('button', { name: 'Edit' }).click();

		const modal = page.locator('div.modal[role="dialog"]');
		await expect(modal).toBeVisible();

		// The .activity-section renders only when audit entries exist
		// for the invoice (loadAuditLog populates it). Seed creates at
		// least one audit row per invoice (creation event).
		await expect(modal.locator('.activity-section .activity-title')).toHaveText(
			'Activity',
			{ timeout: 10_000 }
		);
	});

	test('modal close button dismisses', async ({ page }) => {
		await page.locator('table tbody tr').first().getByRole('button', { name: 'Edit' }).click();
		const modal = page.locator('div.modal[role="dialog"]');
		await expect(modal).toBeVisible();

		await modal.getByRole('button', { name: 'Close' }).click();
		await expect(modal).toBeHidden();
	});
});
