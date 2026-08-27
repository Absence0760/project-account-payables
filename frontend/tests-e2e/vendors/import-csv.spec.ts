import { expect, tenantPsql, test } from '../fixtures/helpers';

/**
 * /vendors — Day-0 CSV import. `POST /api/vendors/import-csv` is
 * skip-and-report (never all-or-nothing): a bad row is counted +
 * explained, the rest of the batch still lands. This exercises the
 * `ImportCsvModal` UI wired into the vendors page — file pick, upload,
 * result rendering, list refresh — not the importer's parsing rules
 * themselves (covered by backend pytest against `services/csv_import.py`).
 */

test.describe('/vendors — Import CSV', () => {
	const marker = `E2E-IMPORT-${Date.now()}`;

	test.afterEach(async () => {
		tenantPsql(`DELETE FROM sanctions_checks WHERE vendor_id IN (SELECT id FROM vendors WHERE name LIKE '${marker}%')`);
		tenantPsql(`DELETE FROM vendors WHERE name LIKE '${marker}%'`);
	});

	test('imports a CSV, reports the row-level result, and the new vendor appears in the list', async ({ page }) => {
		await page.goto('/vendors');
		await page.waitForLoadState('networkidle');

		await page.getByRole('button', { name: 'Import CSV' }).click();
		const modal = page.getByRole('dialog', { name: 'Import CSV' });
		await expect(modal).toBeVisible();
		// Column guide renders before a file is picked.
		await expect(modal.getByText(/Only.*name.*required/)).toBeVisible();

		const goodName = `${marker} Good Co`;
		const csv = `name,code,payment_terms\n${goodName},${marker}-CODE,Net 30\n,${marker}-NOROW,Net 15\n`;
		await modal.locator('input[type="file"]').setInputFiles({
			name: 'vendors.csv',
			mimeType: 'text/csv',
			buffer: Buffer.from(csv)
		});

		const importButton = modal.getByRole('button', { name: 'Import' });
		await expect(importButton).toBeEnabled();
		await importButton.click();

		// Skip-and-report: one good row imported, one bad row (missing name)
		// skipped with an explanation, not a hard failure of the whole batch.
		await expect(modal.getByText('1 imported, 1 skipped')).toBeVisible({ timeout: 15_000 });
		await expect(modal.locator('.error-list li')).toHaveCount(1);
		await expect(modal.locator('.error-list li')).toContainText(/row 3/i);

		await modal.getByRole('button', { name: 'Close' }).click();
		await expect(modal).not.toBeVisible();

		// List refetched — the newly-imported vendor is visible without a
		// manual page reload, landed as `unverified` per the import contract.
		await page.getByRole('textbox', { name: /search vendors/i }).fill(goodName);
		// Scoped to the vendor-name cell specifically — the bulk-select
		// checkbox cell's own aria-label ("Select {name}") also contains the
		// vendor name as a substring, so an unscoped role query resolves
		// ambiguously to both cells.
		await expect(page.locator('td.vendor-name', { hasText: goodName })).toBeVisible({
			timeout: 15_000
		});
	});

	test('a hard failure (non-CSV upload) surfaces as an error toast, not a silent no-op', async ({ page }) => {
		await page.goto('/vendors');
		await page.waitForLoadState('networkidle');
		await page.getByRole('button', { name: 'Import CSV' }).click();
		const modal = page.getByRole('dialog', { name: 'Import CSV' });

		// Non-UTF-8 bytes — the backend's own documented hard-failure case
		// (`CSV must be UTF-8 encoded`), distinct from a row-level skip.
		await modal.locator('input[type="file"]').setInputFiles({
			name: 'bad.csv',
			mimeType: 'text/csv',
			buffer: Buffer.from([0xff, 0xfe, 0x00, 0x00, 0x6e, 0x61, 0x6d, 0x65])
		});
		await modal.getByRole('button', { name: 'Import' }).click();

		await expect(page.getByText(/UTF-8/i)).toBeVisible({ timeout: 15_000 });
	});
});
