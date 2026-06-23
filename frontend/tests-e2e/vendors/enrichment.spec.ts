import { API_BASE, authedTenantHeaders, expect, signInAndWait, tenantPsql, test } from '../fixtures/helpers';

/**
 * /vendors — external firmographics enrichment "Apply" flow in VendorModal.
 *
 * The local-first `mock` enrichment adapter (the dev/test default) returns a
 * deterministic match for any vendor: legal name `<name> (MOCK)`, a fixed
 * address "1 Mock Plaza, Suite 100", and a website. So enriching a fresh vendor
 * (one whose name / address / website don't yet match the mock values) reliably
 * yields a per-field diff (name / address / website).
 *
 * Each test creates its own vendor via the API to guarantee a pristine starting
 * state — "first vendor in the list" approaches are fragile because a prior run
 * that applied enrichment produces a vendor already matching the mock suggestions,
 * which makes the Address row (and possibly the name row) disappear from the diff.
 *
 * Advisory framing: nothing is auto-applied; tax_id is never applyable here.
 */

const VENDOR_MARKER = 'ENRICH-TEST-';

/** Create a fresh vendor guaranteed to differ from mock enrichment suggestions. */
async function createTestVendor(
	page: import('@playwright/test').Page,
	name: string
): Promise<{ id: string; name: string }> {
	const resp = await page.request.post(`${API_BASE}/api/vendors`, {
		headers: await authedTenantHeaders(page),
		data: { name }
	});
	expect(resp.status()).toBe(201);
	return (await resp.json()) as { id: string; name: string };
}

/** Soft-delete the test vendor (the vendor API has no DELETE; use SQL). */
function deleteTestVendor(id: string): void {
	try {
		tenantPsql(`DELETE FROM exceptions WHERE vendor_id='${id}'`);
		tenantPsql(`DELETE FROM invoices WHERE vendor_id='${id}'`);
		tenantPsql(`DELETE FROM sanctions_checks WHERE vendor_id='${id}'`);
		tenantPsql(`DELETE FROM vendors WHERE id='${id}'`);
	} catch {
		/* best-effort */
	}
}

test.describe('/vendors external enrichment (admin)', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/vendors');
		await page.waitForLoadState('networkidle');
	});

	test('enrich renders the suggestion diff, then apply updates the row', async ({ page }) => {
		const vendor = await createTestVendor(page, `${VENDOR_MARKER}${Date.now()}`);
		try {
			// Search for the fresh vendor so it appears as the first row.
			const searchBox = page.getByPlaceholder('Search vendors...');
			await searchBox.fill(vendor.name);
			await page.waitForResponse((r) => r.url().includes('/api/vendors') && r.url().includes('search='));

			const firstRow = page.locator('table tbody tr').first();
			await firstRow.locator('td.vendor-name .row-link').click();
			const modal = page.getByRole('dialog', { name: 'Vendor screening and risk' });
			await expect(modal).toBeVisible();

			// The enrichment panel + action are present for an admin.
			await expect(modal.getByRole('heading', { name: 'External enrichment' })).toBeVisible();
			const enrichBtn = modal.getByRole('button', { name: 'Enrich from external source' });
			await expect(enrichBtn).toBeVisible();

			await enrichBtn.click();

			// The diff table renders with the current → suggested columns. The mock
			// always suggests Legal name, Address, and Website on a fresh vendor.
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
		} finally {
			deleteTestVendor(vendor.id);
		}
	});

	test('idempotent re-enrich after apply shows no further name suggestion', async ({ page }) => {
		const vendor = await createTestVendor(page, `${VENDOR_MARKER}${Date.now()}`);
		try {
			const searchBox = page.getByPlaceholder('Search vendors...');
			await searchBox.fill(vendor.name);
			await page.waitForResponse((r) => r.url().includes('/api/vendors') && r.url().includes('search='));

			const firstRow = page.locator('table tbody tr').first();
			await firstRow.locator('td.vendor-name .row-link').click();
			const modal = page.getByRole('dialog', { name: 'Vendor screening and risk' });
			await expect(modal).toBeVisible();

			await modal.getByRole('button', { name: 'Enrich from external source' }).click();
			await expect(modal.locator('table.enrich-diff')).toBeVisible();
			// Apply everything that's suggested.
			await modal.getByRole('button', { name: /^Apply selected/ }).click();
			await expect(modal.locator('table.enrich-diff')).toBeHidden();

			// Re-enrich: all three fields (name / address / website) now already
			// match the mock adapter's canonical output, so there should be no
			// suggestions at all.  Wait on the enrich API response so the UI has
			// had time to settle before we inspect it.
			const reEnrichDone = page.waitForResponse(
				(r) =>
					r.url().includes('/api/enrichment/vendors/') &&
					r.url().includes('/enrich') &&
					r.request().method() === 'POST' &&
					r.status() === 200
			);
			await modal.getByRole('button', { name: 'Enrich from external source' }).click();
			await reEnrichDone;
			// The mock adapter is idempotent: a second call for a vendor already
			// carrying the mock values returns an empty suggestions list, so the
			// diff table is gone and the "No suggested changes" note appears.
			const emptyNote = modal.getByText('No suggested changes', { exact: false });
			await expect(emptyNote).toBeVisible();
		} finally {
			deleteTestVendor(vendor.id);
		}
	});
});

test.describe('/vendors external enrichment (clerk has no access)', () => {
	// The enrich + apply endpoints are admin/ap_manager/cfo only.
	// `GET /api/vendors` also excludes ap_clerk (ROLE_ADMIN, ROLE_AP_MANAGER,
	// ROLE_CFO only), so the UI vendor list is empty for a clerk and the modal
	// can't be opened through the UI. We test the RBAC invariant directly via
	// the API: POST /api/enrichment/vendors/{id}/enrich must return 403.
	test.use({ storageState: { cookies: [], origins: [] } });

	test('ap_clerk cannot call the enrich API (403)', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);
		// Pick any seeded vendor id via SQL (clerk can't list vendors via the API).
		const vendorId = tenantPsql(`SELECT id FROM vendors LIMIT 1`).trim();
		expect(vendorId).toMatch(/[0-9a-f-]{36}/);

		const headers = await authedTenantHeaders(page);
		const resp = await page.request.post(
			`${API_BASE}/api/enrichment/vendors/${vendorId}/enrich`,
			{ headers }
		);
		// Enrich endpoint is gated to admin / ap_manager / cfo — clerk gets 403.
		expect(resp.status()).toBe(403);
	});
});
