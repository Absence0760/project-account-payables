import { expect, signInAndWait, test } from '../fixtures/helpers';

/**
 * Manual invoice entry — "+ Create Invoice" on the /invoices toolbar opens
 * `CreateInvoiceModal`, which POSTs the keyed-in fields to the pre-existing
 * `POST /api/invoices` (creates at status `new`, no extraction) and, if a
 * file was picked, follows up with `POST /api/invoices/{id}/file` (new
 * attach-only endpoint — refuses once the invoice already has a file).
 *
 * Roadmap item "Manual Invoice Entry (No-OCR Creation)" in
 * docs/roadmap.md § Priority 1.
 */

test.describe('/invoices — Create Invoice modal', () => {
	test('admin sees the toolbar button; required fields gate submit', async ({ page }) => {
		await page.goto('/invoices');

		const button = page.getByRole('button', { name: 'Create Invoice' });
		await expect(button).toBeVisible();
		await button.click();

		const modal = page.locator('div.modal[role="dialog"][aria-label="Create Invoice"]');
		await expect(modal).toBeVisible();

		const submitBtn = modal.getByRole('button', { name: 'Create' });
		await expect(submitBtn).toBeDisabled();

		await modal.locator('label', { hasText: 'Vendor' }).locator('input').fill('E2E Manual Vendor');
		await expect(submitBtn).toBeDisabled();
		await modal.locator('label', { hasText: 'Invoice #' }).locator('input').fill('E2E-MANUAL-1');
		await expect(submitBtn).toBeDisabled();
		await modal.locator('label', { hasText: 'Amount' }).locator('input').fill('42.50');
		await expect(submitBtn).toBeEnabled();

		await modal.getByRole('button', { name: 'Cancel' }).click();
		await expect(modal).not.toBeVisible();
	});

	test('creating without a file lands the invoice at New in the list', async ({ page }) => {
		await page.goto('/invoices');
		const uniqueNumber = `E2E-MANUAL-${Date.now()}`;

		await page.getByRole('button', { name: 'Create Invoice' }).click();
		const modal = page.locator('div.modal[role="dialog"][aria-label="Create Invoice"]');
		await expect(modal).toBeVisible();

		await modal.locator('label', { hasText: 'Vendor' }).locator('input').fill('E2E Manual Vendor');
		await modal.locator('label', { hasText: 'Invoice #' }).locator('input').fill(uniqueNumber);
		await modal.locator('label', { hasText: 'Amount' }).locator('input').fill('42.50');
		await modal.getByRole('button', { name: 'Create' }).click();

		await expect(modal).not.toBeVisible({ timeout: 10_000 });

		const row = page.locator('table tbody tr', { hasText: uniqueNumber });
		await expect(row).toBeVisible({ timeout: 10_000 });
		await expect(row).toContainText('New');
	});

	test('creating with a file attaches it — Activity timeline shows the attach event', async ({
		page
	}) => {
		await page.goto('/invoices');
		const uniqueNumber = `E2E-MANUAL-FILE-${Date.now()}`;

		await page.getByRole('button', { name: 'Create Invoice' }).click();
		const modal = page.locator('div.modal[role="dialog"][aria-label="Create Invoice"]');
		await expect(modal).toBeVisible();

		await modal.locator('label', { hasText: 'Vendor' }).locator('input').fill('E2E File Vendor');
		await modal.locator('label', { hasText: 'Invoice #' }).locator('input').fill(uniqueNumber);
		await modal.locator('label', { hasText: 'Amount' }).locator('input').fill('99.00');
		await modal
			.locator('input[type="file"]')
			.setInputFiles({ name: 'invoice.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 fake') });
		await modal.getByRole('button', { name: 'Create' }).click();

		await expect(modal).not.toBeVisible({ timeout: 10_000 });

		const row = page.locator('table tbody tr', { hasText: uniqueNumber });
		await expect(row).toBeVisible({ timeout: 10_000 });
		await row.getByRole('button', { name: 'Edit' }).click();

		const detail = page.locator('div.modal[role="dialog"]', { hasText: uniqueNumber });
		await expect(detail).toBeVisible();
		await expect(detail.locator('.activity-action', { hasText: 'File attached' })).toBeVisible({
			timeout: 5_000
		});
	});

	test('ap_clerk does not see the toolbar button', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);
		await page.goto('/invoices');
		await page.waitForLoadState('networkidle');
		await expect(page.getByRole('button', { name: 'Create Invoice' })).toHaveCount(0);
	});
});
