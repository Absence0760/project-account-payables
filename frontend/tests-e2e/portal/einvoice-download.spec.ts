import type { Page } from '@playwright/test';

import {
	acceptConsent,
	currentTenantSlug,
	deleteInvoicesWhere,
	expect,
	tenantPsql,
	test
} from '../fixtures/helpers';

/**
 * Supplier portal — the vendor-scoped e-invoice download.
 *
 * `GET /api/portal/invoices/{id}/einvoice` has shipped since the outbound UBL
 * generator landed and had no caller in the portal, so a supplier could not
 * obtain a structured copy of the invoice as their customer now holds it —
 * only the source document they themselves uploaded.
 *
 * Auth + seed shape mirror `rejected-invoice.spec.ts`: the seeded portal
 * vendor user, rows inserted by SQL and removed in a `finally`.
 */

const PORTAL_EMAIL = 'supplier@portal.test';
const PORTAL_PASSWORD = 'demo';
const MARKER = 'E2E-PEINV';

test.use({ storageState: { cookies: [], origins: [] } });

async function portalSignIn(page: Page) {
	await acceptConsent(page);
	await page.goto('/portal/login');
	await page.waitForLoadState('networkidle');
	await page.locator('input[type="email"]').fill(PORTAL_EMAIL);
	await page.locator('input[type="password"]').fill(PORTAL_PASSWORD);
	await page.locator('button[type="submit"]').click();
	await expect(page).toHaveURL(/\/portal\/?$/, { timeout: 15_000 });
	await page.goto('/portal/invoices');
	await page.waitForLoadState('networkidle');
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
	deleteInvoicesWhere(`invoice_number LIKE '${MARKER}%'`);
}

test.afterEach(cleanup);

test('a supplier can download the structured e-invoice for their own invoice', async ({
	page
}) => {
	const { vendorId, orgId } = portalVendor();
	cleanup();
	const num = `${MARKER}-${Date.now()}`;

	try {
		const invId = crypto.randomUUID();
		tenantPsql(
			`INSERT INTO invoices
			   (id, correlation_id, invoice_number, vendor_name, amount, currency, status,
			    invoice_date, organization_id, vendor_id, created_at, updated_at)
			 VALUES ('${invId}', gen_random_uuid(), '${num}', 'E2E Portal Vendor',
			         420.00, 'USD', 'approved', DATE '2026-01-15', '${orgId}', '${vendorId}',
			         now() + interval '1 hour', now())`
		);
		tenantPsql(
			`INSERT INTO invoice_line_items
			   (id, invoice_id, line_number, description, quantity, unit_price, total,
			    created_at, updated_at)
			 VALUES (gen_random_uuid(), '${invId}', 1, 'Consulting', 1.0000, 420.00, 420.00,
			         now(), now())`
		);

		await portalSignIn(page);

		const row = page.locator('tr.clickable', { hasText: num });
		await expect(row).toHaveCount(1, { timeout: 10_000 });

		const download = page.waitForEvent('download');
		await row.getByTestId('portal-einvoice-download').click();
		const file = await download;
		expect(file.suggestedFilename()).toBe(`einvoice-${num}.xml`);

		// The portal never 422s a supplier on a tax soft-warning, so no error
		// banner may appear on the happy path.
		await expect(page.locator('.error')).toHaveCount(0);
	} finally {
		cleanup();
	}
});
