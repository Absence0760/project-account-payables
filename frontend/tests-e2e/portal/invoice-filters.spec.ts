import type { Page } from '@playwright/test';

import { currentTenantSlug, expect, test, tenantPsql } from '../fixtures/helpers';

/**
 * Supplier-portal invoice list — status + invoice-number filters.
 *
 * persona-supplier finding (issue #328): the vendor's own invoice list was
 * "Load more" only — no way to jump to a rejected invoice or find one by
 * number in a long history. The fix adds a debounced number search and a row
 * of vendor-facing phase chips (`PORTAL_INVOICE_PHASES`, which collapse the 12
 * internal `InvoiceStatus` values into the handful a supplier sees) that send
 * `?status=` / `?search=` to `GET /api/portal/invoices`.
 *
 * This spec seeds three invoices for the seeded portal vendor with distinct,
 * test-owned numbers across three phases, then drives the real controls and
 * asserts on the PRESENCE / ABSENCE of those three rows — never on a total,
 * so it can't drift with whatever else the vendor already has.
 *
 * Auth + seed shape mirror pagination.spec.ts.
 */

const PORTAL_EMAIL = 'supplier@portal.test';
const PORTAL_PASSWORD = 'demo';
const PREFIX = 'E2E-FILT';

test.use({ storageState: { cookies: [], origins: [] } });

async function portalSignIn(page: Page) {
	await page.addInitScript(() => {
		try {
			localStorage.setItem('feoh_consent_choice', 'accepted');
		} catch {
			/* about:blank — ignore */
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
	expect(orgId).not.toEqual('');
	return { vendorId, orgId };
}

test.afterEach(() => {
	tenantPsql(`DELETE FROM invoices WHERE invoice_number LIKE '${PREFIX}-%'`);
});

test.describe('/portal/invoices — filters', () => {
	test('phase chips and number search narrow the list without widening it', async ({ page }) => {
		const { vendorId, orgId } = portalVendor();
		tenantPsql(`DELETE FROM invoices WHERE invoice_number LIKE '${PREFIX}-%'`);

		try {
			tenantPsql(
				`INSERT INTO invoices
				   (id, correlation_id, invoice_number, vendor_name, amount, currency, status,
				    organization_id, vendor_id, created_at, updated_at)
				 VALUES
				   (gen_random_uuid(), gen_random_uuid(), '${PREFIX}-NEW',  'E2E Filter Vendor',
				    100, 'USD', 'new',      '${orgId}', '${vendorId}', now() + interval '1 hour', now()),
				   (gen_random_uuid(), gen_random_uuid(), '${PREFIX}-PAID', 'E2E Filter Vendor',
				    200, 'USD', 'paid',     '${orgId}', '${vendorId}', now() + interval '59 minutes', now()),
				   (gen_random_uuid(), gen_random_uuid(), '${PREFIX}-REJ',  'E2E Filter Vendor',
				    300, 'USD', 'rejected', '${orgId}', '${vendorId}', now() + interval '58 minutes', now())`
			);

			await portalSignIn(page);

			const newRow = page.locator('tr.clickable', { hasText: `${PREFIX}-NEW` });
			const paidRow = page.locator('tr.clickable', { hasText: `${PREFIX}-PAID` });
			const rejRow = page.locator('tr.clickable', { hasText: `${PREFIX}-REJ` });

			// Unfiltered: all three seeded rows are present.
			await expect(newRow).toHaveCount(1, { timeout: 10_000 });
			await expect(paidRow).toHaveCount(1);
			await expect(rejRow).toHaveCount(1);

			// "Paid" phase chip → only the paid row of the three.
			await page.getByRole('button', { name: 'Paid', exact: true }).click();
			await expect(paidRow).toHaveCount(1);
			await expect(newRow).toHaveCount(0);
			await expect(rejRow).toHaveCount(0);

			// Back to "All".
			await page.getByRole('button', { name: 'All', exact: true }).click();
			await expect(newRow).toHaveCount(1);
			await expect(rejRow).toHaveCount(1);

			// Number search is a substring match, debounced.
			await page.getByLabel('Search invoices').fill(`${PREFIX}-REJ`);
			await expect(rejRow).toHaveCount(1);
			await expect(newRow).toHaveCount(0);
			await expect(paidRow).toHaveCount(0);

			// A term that matches nothing shows the filtered-empty state + a way out.
			await page.getByLabel('Search invoices').fill(`${PREFIX}-NOPE`);
			await expect(page.getByText('No invoices match your filters.')).toBeVisible();
			await page.getByRole('button', { name: 'Clear filters' }).click();
			await expect(newRow).toHaveCount(1);
			await expect(paidRow).toHaveCount(1);
			await expect(rejRow).toHaveCount(1);
		} finally {
			tenantPsql(`DELETE FROM invoices WHERE invoice_number LIKE '${PREFIX}-%'`);
		}
	});
});
