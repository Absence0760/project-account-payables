import { expect, test } from '../fixtures/helpers';
import { signInAndWait } from '../fixtures/helpers';

/**
 * /vendors — external firmographics enrichment "Apply" flow in VendorModal.
 *
 * The local-first `mock` enrichment adapter (the dev/test default) returns a
 * deterministic match for any seeded vendor: legal name `<name> (MOCK)`, a
 * fixed address, and a website. So enriching any vendor reliably yields a
 * per-field diff (name / address / website). The steward picks fields and
 * applies them; the apply is audited + idempotent on the backend, and the
 * vendor name in the list updates to the applied legal name.
 *
 * Advisory framing: nothing is auto-applied; tax_id is never applyable here.
 */

async function openFirstVendorModal(page: import('@playwright/test').Page) {
	const firstRow = page.locator('table tbody tr').first();
	await firstRow.locator('td.vendor-name .row-link').click();
	const modal = page.getByRole('dialog', { name: 'Vendor screening and risk' });
	await expect(modal).toBeVisible();
	return modal;
}

test.describe('/vendors external enrichment (admin)', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/vendors');
		await page.waitForLoadState('networkidle');
	});

	test('enrich renders the suggestion diff, then apply updates the row', async ({ page }) => {
		const modal = await openFirstVendorModal(page);

		// The enrichment panel + action are present for an admin.
		await expect(modal.getByRole('heading', { name: 'External enrichment' })).toBeVisible();
		const enrichBtn = modal.getByRole('button', { name: 'Enrich from external source' });
		await expect(enrichBtn).toBeVisible();

		await enrichBtn.click();

		// The diff table renders with the current → suggested columns. The mock
		// always suggests at least a Legal name change (`<name> (MOCK)`).
		const diff = modal.locator('table.enrich-diff');
		await expect(diff).toBeVisible();
		await expect(diff.getByText('Legal name')).toBeVisible();
		await expect(diff.getByText('Address')).toBeVisible();

		// The Legal-name checkbox is pre-checked (steward chooses what to keep).
		const nameApply = modal.getByRole('checkbox', { name: 'Apply Legal name' });
		await expect(nameApply).toBeChecked();

		// Deselect Address so only the chosen subset is applied (non-destructive).
		const addressApply = modal.getByRole('checkbox', { name: 'Apply Address' });
		await addressApply.uncheck();

		// Apply the selection.
		const applyBtn = modal.getByRole('button', { name: /^Apply selected/ });
		await expect(applyBtn).toBeVisible();
		await applyBtn.click();

		// The diff clears after a successful apply (the values are now current).
		await expect(diff).toBeHidden();

		// The vendor's name in the modal heading now carries the applied
		// `(MOCK)` legal name. (onupdated propagates the apply response's vendor.)
		await expect(modal.getByRole('heading', { level: 2 })).toContainText('(MOCK)');
	});

	test('idempotent re-enrich after apply shows no further name suggestion', async ({ page }) => {
		const modal = await openFirstVendorModal(page);
		await modal.getByRole('button', { name: 'Enrich from external source' }).click();
		await expect(modal.locator('table.enrich-diff')).toBeVisible();
		// Apply everything that's suggested.
		await modal.getByRole('button', { name: /^Apply selected/ }).click();
		await expect(modal.locator('table.enrich-diff')).toBeHidden();

		// Re-enrich: the name now already equals `<name> (MOCK)`, so the Legal
		// name row should no longer be suggested (the diff omits unchanged fields).
		await modal.getByRole('button', { name: 'Enrich from external source' }).click();
		const diff = modal.locator('table.enrich-diff');
		// Either an empty-state note or a diff without the Legal name row.
		const emptyNote = modal.getByText('No suggested changes', { exact: false });
		if (await diff.isVisible().catch(() => false)) {
			await expect(diff.getByText('Legal name')).toHaveCount(0);
		} else {
			await expect(emptyNote).toBeVisible();
		}
	});
});

test.describe('/vendors external enrichment (clerk has no access)', () => {
	// The enrich + apply endpoints are admin/ap_manager/cfo — a clerk must not
	// see the action at all.
	test.use({ storageState: { cookies: [], origins: [] } });

	test('ap_clerk does not see the enrichment action', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);
		await page.goto('/vendors');
		await page.waitForLoadState('networkidle');

		const firstRow = page.locator('table tbody tr').first();
		await firstRow.locator('td.vendor-name .row-link').click();
		const modal = page.getByRole('dialog', { name: 'Vendor screening and risk' });
		await expect(modal).toBeVisible();

		// The clerk sees the screening panel but NOT the enrichment panel/action.
		await expect(modal.getByRole('heading', { name: 'External enrichment' })).toHaveCount(0);
		await expect(
			modal.getByRole('button', { name: 'Enrich from external source' })
		).toHaveCount(0);
	});
});
