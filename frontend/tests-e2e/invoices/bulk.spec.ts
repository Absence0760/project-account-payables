import { expect, test } from '../fixtures/helpers';

/**
 * /invoices — bulk operations bar.
 *
 * Selecting any row reveals the bulk-bar with "X selected", a Clear
 * button, a Change Status dropdown, a Delete button, and CSV/JSON/XML
 * export buttons. System-managed statuses can't be selected (the row
 * checkbox is disabled), so this spec selects rows whose status is
 * known to be selectable (new / ready_for_review / approved / rejected).
 */

test.describe('/invoices bulk bar (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/invoices');
		await expect(page.locator('table tbody tr').first()).toBeVisible();
	});

	test('bulk bar appears after selecting a row', async ({ page }) => {
		await expect(page.locator('.bulk-bar')).toHaveCount(0);
		// Pick the first row whose checkbox is enabled (i.e. not system-
		// managed). The seed always has at least one such row.
		const enabledCheckbox = page
			.locator('table tbody tr td.checkbox-col input[type="checkbox"]:not([disabled])')
			.first();
		await enabledCheckbox.check();

		await expect(page.locator('.bulk-bar')).toBeVisible();
		await expect(page.locator('.bulk-count')).toContainText('1 selected');
	});

	test('Clear deselects everything and hides the bar', async ({ page }) => {
		const enabledCheckbox = page
			.locator('table tbody tr td.checkbox-col input[type="checkbox"]:not([disabled])')
			.first();
		await enabledCheckbox.check();
		await expect(page.locator('.bulk-bar')).toBeVisible();

		await page.locator('.bulk-bar button.bulk-clear').click();
		await expect(page.locator('.bulk-bar')).toHaveCount(0);
	});

	test('selecting two rows shows "2 selected"', async ({ page }) => {
		const enabled = page.locator(
			'table tbody tr td.checkbox-col input[type="checkbox"]:not([disabled])'
		);
		await enabled.nth(0).check();
		await enabled.nth(1).check();
		await expect(page.locator('.bulk-count')).toContainText('2 selected');
	});

	test('Change Status, Delete, and CSV/JSON/XML buttons are present', async ({
		page
	}) => {
		await page
			.locator('table tbody tr td.checkbox-col input[type="checkbox"]:not([disabled])')
			.first()
			.check();

		const bar = page.locator('.bulk-bar');
		await expect(bar.getByRole('button', { name: 'Change Status' })).toBeVisible();
		await expect(bar.getByRole('button', { name: 'Delete' })).toBeVisible();
		await expect(bar.getByRole('button', { name: 'CSV' })).toBeVisible();
		await expect(bar.getByRole('button', { name: 'JSON' })).toBeVisible();
		await expect(bar.getByRole('button', { name: 'XML' })).toBeVisible();
	});

	test('Change Status dropdown opens with the valid transitions', async ({ page }) => {
		await page
			.locator('table tbody tr td.checkbox-col input[type="checkbox"]:not([disabled])')
			.first()
			.check();
		await page.getByRole('button', { name: 'Change Status' }).click();

		const dropdown = page.locator('.bulk-status-dropdown select');
		await expect(dropdown).toBeVisible();
		// The select has at least one option (whichever state the
		// selected row's status can transition to per VALID_TRANSITIONS).
		const optionCount = await dropdown.locator('option').count();
		expect(optionCount).toBeGreaterThan(0);
	});
});
