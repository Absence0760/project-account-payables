import { expect, test } from '../fixtures/helpers';

/**
 * Clickable list rows (RowLink + isRowOpenClick).
 *
 * The per-row "Edit" button was replaced by making the whole row open the
 * editor: the invoice-number cell is a real `<RowLink>` (focusable / keyboard
 * operable) and the `<tr>` carries a pointer `onclick` gated by
 * `isRowOpenClick`, so the bulk-select checkbox and the kept Delete button
 * still work without opening the modal.
 *
 * These tests lock the three contracts that pattern depends on:
 *   1. Clicking a plain (non-control) cell opens the editor.
 *   2. Keyboard: focusing the invoice-number RowLink and pressing Enter opens it.
 *   3. Clicking Delete does NOT open the editor — it arms the confirm instead.
 */
test.describe('/invoices clickable rows', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/invoices');
		await expect(page.locator('table tbody tr').first()).toBeVisible();
	});

	test('clicking a plain cell on the row opens the edit modal', async ({ page }) => {
		const row = page.locator('table tbody tr').first();
		// Column 2 is the Vendor cell — a plain, non-interactive cell, not the
		// invoice-number RowLink (col 1), checkbox (col 0), or actions (last).
		await row.locator('td').nth(2).click();

		await expect(
			page.locator('div.modal[role="dialog"][aria-label*="Edit invoice"]')
		).toBeVisible();
	});

	test('the invoice-number RowLink opens the modal via keyboard (Enter)', async ({ page }) => {
		const link = page
			.locator('table tbody tr')
			.first()
			.getByRole('button', { name: /^Edit invoice/ });
		await link.focus();
		await expect(link).toBeFocused();
		await link.press('Enter');

		await expect(
			page.locator('div.modal[role="dialog"][aria-label*="Edit invoice"]')
		).toBeVisible();
	});

	test('clicking Delete on a row arms the confirm and does NOT open the modal', async ({
		page
	}) => {
		// Pick a row that actually exposes a Delete button (non-immutable status).
		const deleteBtn = page
			.locator('table tbody tr')
			.filter({ has: page.getByRole('button', { name: 'Delete' }) })
			.first()
			.getByRole('button', { name: 'Delete' });
		await expect(deleteBtn).toBeVisible();

		await deleteBtn.click();

		// The editor must stay closed — the row-open guard swallowed the click.
		await expect(page.locator('div.modal[role="dialog"][aria-label*="Edit invoice"]')).toHaveCount(
			0
		);
		// Instead the destructive action armed into its two-step confirm. Only one
		// row can be armed at a time, so a single page-level Confirm appears.
		// (Re-using the `Delete`-filtered row locator would miss it — that row no
		// longer matches once its button text flips to "Confirm".)
		await expect(page.getByRole('button', { name: 'Confirm' })).toBeVisible();
	});
});
