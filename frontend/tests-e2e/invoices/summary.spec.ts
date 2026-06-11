import { expect, test } from '../fixtures/helpers';

/**
 * Audit-log summary block — rendered at the TOP of the invoice detail modal
 * (above the field grid). It is fetched lazily from
 * `GET /api/invoices/{id}/summary` on modal open and, in local dev (no
 * Anthropic key), comes back as the deterministic template summary — so this
 * spec is stable without a live LLM.
 *
 * Seed creates at least one audit row per invoice, so every seeded invoice
 * has a non-empty summary paragraph. Admins/managers additionally see a
 * "Regenerate" button.
 */

test.describe('/invoices invoice summary', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/invoices');
		await expect(page.locator('table tbody tr').first()).toBeVisible();
	});

	test('summary paragraph renders at the top of the modal', async ({ page }) => {
		await page.locator('table tbody tr').first().getByRole('button', { name: 'Edit' }).click();

		const modal = page.locator('div.modal[role="dialog"]');
		await expect(modal).toBeVisible();

		const summary = modal.locator('[data-testid="audit-summary"]');
		await expect(summary).toBeVisible({ timeout: 10_000 });

		const text = modal.locator('[data-testid="audit-summary-text"]');
		await expect(text).toBeVisible();
		// Template summary always leads with "Invoice <number> from <vendor>".
		await expect(text).toContainText('Invoice');
	});

	test('summary appears above the vendor field', async ({ page }) => {
		await page.locator('table tbody tr').first().getByRole('button', { name: 'Edit' }).click();
		const modal = page.locator('div.modal[role="dialog"]');
		await expect(modal).toBeVisible();

		const summaryText = modal.locator('[data-testid="audit-summary-text"]');
		await expect(summaryText).toBeVisible({ timeout: 10_000 });

		// The summary block must precede the form grid (the vendor input).
		const vendorInput = modal.locator('.form-grid input').first();
		const summaryBox = await summaryText.boundingBox();
		const vendorBox = await vendorInput.boundingBox();
		expect(summaryBox).not.toBeNull();
		expect(vendorBox).not.toBeNull();
		expect(summaryBox!.y).toBeLessThan(vendorBox!.y);
	});

	test('admin can regenerate the summary', async ({ page }) => {
		await page.locator('table tbody tr').first().getByRole('button', { name: 'Edit' }).click();
		const modal = page.locator('div.modal[role="dialog"]');
		await expect(modal).toBeVisible();

		const regen = modal.locator('[data-testid="audit-summary-regenerate"]');
		await expect(regen).toBeVisible({ timeout: 10_000 });

		// Clicking it issues POST .../summary/regenerate and refreshes the
		// paragraph (which remains non-empty afterwards).
		await regen.click();
		await expect(modal.locator('[data-testid="audit-summary-text"]')).toContainText('Invoice', {
			timeout: 10_000
		});
	});
});
