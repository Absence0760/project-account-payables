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
 * Supplier-portal invoice + payment lists — status + invoice-number filters.
 *
 * persona-supplier finding (issue #328): the vendor's own lists were "Load
 * more" only — no way to jump to a rejected invoice or find one by number in a
 * long history. The fix (shared `PortalListFilters.svelte`) adds a debounced
 * number search and a row of vendor-facing phase chips that collapse the raw
 * internal statuses into the handful a supplier sees and send `?status=` /
 * `?search=` to `GET /api/portal/{invoices,payments}`.
 *
 * Each spec seeds a few rows for the seeded portal vendor with distinct,
 * test-owned numbers across phases, drives the real controls, and asserts on
 * the PRESENCE / ABSENCE of those rows — never on a total, so it can't drift
 * with whatever else the vendor already has.
 *
 * Auth + seed shape mirror pagination.spec.ts.
 */

const PORTAL_EMAIL = 'supplier@portal.test';
const PORTAL_PASSWORD = 'demo';
const PREFIX = 'E2E-FILT';

test.use({ storageState: { cookies: [], origins: [] } });

async function portalSignIn(page: Page) {
	await acceptConsent(page);
	await page.goto('/portal/login');
	await page.waitForLoadState('networkidle');
	await page.locator('input[type="email"]').fill(PORTAL_EMAIL);
	await page.locator('input[type="password"]').fill(PORTAL_PASSWORD);
	await page.locator('button[type="submit"]').click();
	// Sign-in lands on the portal HOME (it exists to answer "what needs my
	// attention"); this spec exercises the invoice list, so navigate on
	// explicitly rather than depending on where login happens to land.
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
	expect(orgId).not.toEqual('');
	return { vendorId, orgId };
}

function cleanup() {
	tenantPsql(
		`DELETE FROM payments WHERE invoice_id IN (SELECT id FROM invoices WHERE invoice_number LIKE '${PREFIX}-%')`
	);
	deleteInvoicesWhere(`invoice_number LIKE '${PREFIX}-%'`);
}

test.afterEach(cleanup);

test.describe('/portal/invoices — filters', () => {
	test('phase chips and number search narrow the list without widening it', async ({ page }) => {
		const { vendorId, orgId } = portalVendor();
		deleteInvoicesWhere(`invoice_number LIKE '${PREFIX}-%'`);

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
			cleanup();
		}
	});

	test('date-range filter narrows by submitted date', async ({ page }) => {
		const { vendorId, orgId } = portalVendor();
		deleteInvoicesWhere(`invoice_number LIKE '${PREFIX}-%'`);
		try {
			// Two invoices submitted on known past dates.
			tenantPsql(
				`INSERT INTO invoices
				   (id, correlation_id, invoice_number, vendor_name, amount, currency, status,
				    organization_id, vendor_id, created_at, updated_at)
				 VALUES
				   (gen_random_uuid(), gen_random_uuid(), '${PREFIX}-OLD', 'E2E Date Vendor',
				    100, 'USD', 'new', '${orgId}', '${vendorId}', '2026-05-10 12:00+00', now()),
				   (gen_random_uuid(), gen_random_uuid(), '${PREFIX}-NEWER', 'E2E Date Vendor',
				    100, 'USD', 'new', '${orgId}', '${vendorId}', '2026-05-25 12:00+00', now())`
			);
			await portalSignIn(page);

			const oldRow = page.locator('tr.clickable', { hasText: `${PREFIX}-OLD` });
			const newerRow = page.locator('tr.clickable', { hasText: `${PREFIX}-NEWER` });
			await expect(oldRow).toHaveCount(1, { timeout: 10_000 });
			await expect(newerRow).toHaveCount(1);

			// From 2026-05-20 → only the 25th falls in range.
			await page.getByLabel('From date').fill('2026-05-20');
			await expect(newerRow).toHaveCount(1);
			await expect(oldRow).toHaveCount(0);

			// Add a To bound that excludes both → filtered-empty.
			await page.getByLabel('To date').fill('2026-05-21');
			await expect(page.getByText('No invoices match your filters.')).toBeVisible();

			await page.getByRole('button', { name: 'Clear filters' }).click();
			await expect(oldRow).toHaveCount(1);
			await expect(newerRow).toHaveCount(1);
		} finally {
			cleanup();
		}
	});
});

test.describe('/portal/payments — filters', () => {
	test('phase chips and number search narrow the payment history', async ({ page }) => {
		const { vendorId, orgId } = portalVendor();
		cleanup();

		try {
			// Three paid invoices, each with one payment in a different state.
			// Numbers carry PREFIX so the shared `cleanup()` (which deletes
			// PREFIX invoices AND their payments) covers them.
			for (const [num, payStatus] of [
				[`${PREFIX}-PF-DONE`, 'completed'],
				[`${PREFIX}-PF-FAIL`, 'failed'],
				[`${PREFIX}-PF-PEND`, 'pending'],
			] as const) {
				tenantPsql(
					`WITH inv AS (
					   INSERT INTO invoices
					     (id, correlation_id, invoice_number, vendor_name, amount, currency, status,
					      organization_id, vendor_id, created_at, updated_at)
					   VALUES (gen_random_uuid(), gen_random_uuid(), '${num}', 'E2E Pay Filter Vendor',
					           100, 'USD', 'paid', '${orgId}', '${vendorId}', now() + interval '1 hour', now())
					   RETURNING id)
					 INSERT INTO payments (id, invoice_id, amount, method, status, created_at, updated_at)
					 SELECT gen_random_uuid(), inv.id, 100, 'ach', '${payStatus}', now(), now() FROM inv`
				);
			}

			await portalSignIn(page);
			await page.getByRole('link', { name: 'Payments' }).click();
			await expect(page).toHaveURL(/\/portal\/payments/, { timeout: 5_000 });

			const doneRow = page.locator('table tbody tr', { hasText: `${PREFIX}-PF-DONE` });
			const failRow = page.locator('table tbody tr', { hasText: `${PREFIX}-PF-FAIL` });
			const pendRow = page.locator('table tbody tr', { hasText: `${PREFIX}-PF-PEND` });

			await expect(doneRow).toHaveCount(1, { timeout: 10_000 });
			await expect(failRow).toHaveCount(1);
			await expect(pendRow).toHaveCount(1);

			// "Completed" phase → only the completed payment.
			await page.getByRole('button', { name: 'Completed', exact: true }).click();
			await expect(doneRow).toHaveCount(1);
			await expect(failRow).toHaveCount(0);
			await expect(pendRow).toHaveCount(0);

			// Number search narrows within the current (unfiltered) set.
			await page.getByRole('button', { name: 'All', exact: true }).click();
			await page.getByLabel('Search payments').fill(`${PREFIX}-PF-FAIL`);
			await expect(failRow).toHaveCount(1);
			await expect(doneRow).toHaveCount(0);
			await expect(pendRow).toHaveCount(0);

			await page.getByLabel('Search payments').fill(`${PREFIX}-PF-NOPE`);
			await expect(page.getByText('No payments match your filters.')).toBeVisible();
			await page.getByRole('button', { name: 'Clear filters' }).click();
			await expect(doneRow).toHaveCount(1);
		} finally {
			cleanup();
		}
	});
});
