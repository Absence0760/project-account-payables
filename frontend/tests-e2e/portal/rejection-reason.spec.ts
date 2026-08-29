import type { Page } from '@playwright/test';

import { currentTenantSlug, expect, test, tenantPsql } from '../fixtures/helpers';

/**
 * Supplier portal — a rejected invoice shows the vendor WHY (issue #328).
 *
 * `GET /portal/invoices` carries `rejection_reason` (the latest
 * `review_rejected` exception's description) for a `rejected` invoice, and the
 * list renders it under the status pill. The rejecting employee's name is
 * never sent.
 *
 * Auth + seed shape mirror pagination.spec.ts. Both the invoice and its
 * exception row are seeded from SQL and removed in a `finally`.
 */

const PORTAL_EMAIL = 'supplier@portal.test';
const PORTAL_PASSWORD = 'demo';
const NUM = `E2E-REJ-${Date.now()}`;
const REASON = 'PO number is missing from the invoice header';

test.use({ storageState: { cookies: [], origins: [] } });

async function portalSignIn(page: Page) {
	await page.addInitScript(() => {
		try {
			localStorage.setItem('feoh_consent_choice', 'accepted');
		} catch {
			/* ignore */
		}
	});
	await page.goto('/portal/login');
	await page.waitForLoadState('networkidle');
	await page.locator('input[type="email"]').fill(PORTAL_EMAIL);
	await page.locator('input[type="password"]').fill(PORTAL_PASSWORD);
	await page.locator('button[type="submit"]').click();
	await expect(page).toHaveURL(/\/portal\/invoices/, { timeout: 15_000 });
}

function portalVendor(): { vendorId: string; orgId: string } {
	const vendorId = tenantPsql(
		`SELECT vendor_id FROM vendor_users WHERE email='${PORTAL_EMAIL}'`
	).trim();
	expect(vendorId, `no VendorUser seeded for ${currentTenantSlug()}`).not.toEqual('');
	const orgId = tenantPsql(`SELECT organization_id FROM vendors WHERE id='${vendorId}'`).trim();
	return { vendorId, orgId };
}

function cleanup() {
	tenantPsql(
		`DELETE FROM exceptions WHERE invoice_id IN (SELECT id FROM invoices WHERE invoice_number LIKE 'E2E-REJ-%')`
	);
	tenantPsql(`DELETE FROM invoices WHERE invoice_number LIKE 'E2E-REJ-%'`);
}

test.afterEach(cleanup);

test('a rejected invoice tells the vendor why', async ({ page }) => {
	const { vendorId, orgId } = portalVendor();
	cleanup();

	try {
		tenantPsql(
			`WITH inv AS (
			   INSERT INTO invoices
			     (id, correlation_id, invoice_number, vendor_name, amount, currency, status,
			      rejected_by, organization_id, vendor_id, created_at, updated_at)
			   VALUES (gen_random_uuid(), gen_random_uuid(), '${NUM}', 'E2E Rej Vendor',
			           100, 'USD', 'rejected', 'Alice Approver', '${orgId}', '${vendorId}',
			           now() + interval '1 hour', now())
			   RETURNING id)
			 INSERT INTO exceptions
			   (id, invoice_id, exception_type, severity, description, status,
			    organization_id, created_at, updated_at)
			 SELECT gen_random_uuid(), inv.id, 'review_rejected', 'warning',
			        '${REASON}', 'open', '${orgId}', now(), now() FROM inv`
		);

		await portalSignIn(page);

		const row = page.locator('tr.clickable', { hasText: NUM });
		await expect(row).toHaveCount(1, { timeout: 10_000 });
		await expect(row.getByText('Why it was rejected')).toBeVisible();
		await expect(row.getByText(REASON)).toBeVisible();
		// The internal approver name is never surfaced to the vendor.
		await expect(page.getByText('Alice Approver')).toHaveCount(0);
	} finally {
		cleanup();
	}
});
