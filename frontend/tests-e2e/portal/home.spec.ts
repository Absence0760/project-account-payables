import type { Page } from '@playwright/test';

import {
	currentTenantSlug,
	deleteInvoicesWhere,
	expect,
	tenantPsql,
	test,
} from '../fixtures/helpers';

/**
 * Supplier-portal home (`/portal`).
 *
 * `/portal` used to redirect straight to `/portal/invoices`, so a supplier had
 * no at-a-glance answer to *is anything waiting on me?* (`docs/followups.md`).
 * It now renders the whole-set, vendor-scoped `GET /api/portal/summary`.
 *
 * What this pins that the pytest suite can't:
 *  - `/portal` renders the overview instead of bouncing to the list.
 *  - The counts a supplier reads are the WHOLE set, not the loaded page, and
 *    they agree with what the database says for that vendor.
 *  - Money is rendered per currency — a EUR invoice and a USD invoice are two
 *    figures on the page, never one summed number.
 *  - No internal workflow status string and no AP employee name reaches the
 *    supplier's screen.
 *
 * Auth + seed shape mirror `rejected-invoice.spec.ts`.
 */

const PORTAL_EMAIL = 'supplier@portal.test';
const PORTAL_PASSWORD = 'demo';
const PREFIX = 'E2E-HOME';

test.use({ storageState: { cookies: [], origins: [] } });

/** Ids this file seeded — cleanup is keyed on them, never on a field the
 *  pipeline may rewrite. */
const seeded: string[] = [];

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
	await expect(page).toHaveURL(/\/portal\/?$/, { timeout: 15_000 });
}

function portalVendor(): { vendorId: string; orgId: string } {
	const vendorId = tenantPsql(
		`SELECT vendor_id FROM vendor_users WHERE email='${PORTAL_EMAIL}'`
	).trim();
	expect(vendorId, `no VendorUser seeded for ${currentTenantSlug()}`).not.toEqual('');
	const orgId = tenantPsql(`SELECT organization_id FROM vendors WHERE id='${vendorId}'`).trim();
	return { vendorId, orgId };
}

function seedInvoice(
	orgId: string,
	vendorId: string,
	opts: { status: string; amount: string; currency: string; rejectedBy?: string }
): string {
	const id = crypto.randomUUID();
	seeded.push(id);
	const rejectedBy = opts.rejectedBy ? `'${opts.rejectedBy}'` : 'NULL';
	tenantPsql(
		`INSERT INTO invoices
		   (id, correlation_id, invoice_number, vendor_name, amount, currency, status,
		    rejected_by, organization_id, vendor_id, created_at, updated_at)
		 VALUES ('${id}', gen_random_uuid(), '${PREFIX}-${id.slice(0, 8)}', 'E2E Home Vendor',
		         ${opts.amount}, '${opts.currency}', '${opts.status}', ${rejectedBy},
		         '${orgId}', '${vendorId}', now(), now())`
	);
	return id;
}

function cleanup() {
	if (seeded.length > 0) {
		deleteInvoicesWhere(`id IN (${seeded.map((id) => `'${id}'`).join(',')})`);
		seeded.length = 0;
	}
	deleteInvoicesWhere(`invoice_number LIKE '${PREFIX}-%'`);
}

test.afterEach(cleanup);

test('/portal renders the overview instead of bouncing to the invoice list', async ({ page }) => {
	const { vendorId, orgId } = portalVendor();
	cleanup();
	try {
		seedInvoice(orgId, vendorId, { status: 'approved', amount: '250.00', currency: 'USD' });

		await portalSignIn(page);
		await page.goto('/portal');
		await page.waitForLoadState('networkidle');

		// Still on /portal — the redirect is gone.
		await expect(page).toHaveURL(/\/portal\/?$/);
		await expect(page.getByTestId('portal-home-kpis')).toBeVisible({ timeout: 15_000 });
	} finally {
		cleanup();
	}
});

test('the overview counts the whole set and never leaks internals', async ({ page }) => {
	const { vendorId, orgId } = portalVendor();
	cleanup();
	try {
		seedInvoice(orgId, vendorId, {
			status: 'rejected',
			amount: '10.00',
			currency: 'USD',
			rejectedBy: 'Alice Approver',
		});

		await portalSignIn(page);
		await page.goto('/portal');
		await page.waitForLoadState('networkidle');

		const kpis = page.getByTestId('portal-home-kpis');
		await expect(kpis).toBeVisible({ timeout: 15_000 });

		// The rejected invoice is surfaced as the supplier's own next action…
		const attention = page.getByTestId('portal-home-action-required');
		await expect(attention).toBeVisible();
		await expect(attention.getByRole('link')).toBeVisible();

		// …and the count matches the backend's whole-set figure for this vendor,
		// which the summary and the list share a filter builder for.
		const expected = tenantPsql(
			`SELECT count(*) FROM invoices WHERE vendor_id='${vendorId}' AND status='rejected'`
		).trim();
		await expect(kpis).toContainText(expected);

		// The supplier never sees the reviewer who rejected it, nor a raw
		// workflow status string.
		await expect(page.getByText('Alice Approver')).toHaveCount(0);
		for (const internal of ['ready_for_review', 'sending_to_erp', 'posted_in_erp']) {
			await expect(page.getByText(internal, { exact: false })).toHaveCount(0);
		}
	} finally {
		cleanup();
	}
});

test('outstanding money is shown per currency, never summed across them', async ({ page }) => {
	const { vendorId, orgId } = portalVendor();
	cleanup();
	try {
		// Both are "with the customer", so both land in the outstanding rollup.
		seedInvoice(orgId, vendorId, { status: 'approved', amount: '1000.00', currency: 'USD' });
		seedInvoice(orgId, vendorId, { status: 'approved', amount: '2000.00', currency: 'EUR' });

		await portalSignIn(page);
		await page.goto('/portal');
		await page.waitForLoadState('networkidle');

		const totals = page.getByTestId('portal-home-outstanding');
		await expect(totals).toBeVisible({ timeout: 15_000 });
		// One row per currency. A cross-currency sum would collapse them into a
		// single number denominated in nothing.
		await expect(totals.locator('li')).toHaveCount(2);
		await expect(totals).toContainText('$');
		await expect(totals).toContainText('€');
	} finally {
		cleanup();
	}
});
