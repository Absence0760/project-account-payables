import type { Page } from '@playwright/test';

import { currentTenantSlug, deleteInvoicesWhere, expect, tenantPsql, test } from '../fixtures/helpers';

/**
 * Supplier-portal list pagination.
 *
 * Every portal list endpoint returns `{items, total, page, page_size}` with a
 * server-side default of 20 rows (`backend/app/api/pagination.py`). The portal
 * pages used to fetch the bare URL, render `res.items`, and offer no control
 * at all — so a supplier with 25 invoices saw 20 rows, no count, and invoice
 * 21 (with its chat thread) was simply unreachable. Older remittances and PO
 * flips were lost the same way, and a discount offer past the first page
 * expired without ever being shown.
 *
 * These specs pin the two halves of the fix on the two lists that can be
 * seeded cheaply and safely from SQL:
 *   1. the first page really is capped at `page_size` (the cliff exists), and
 *      row 21 is NOT rendered before the user asks for it;
 *   2. Load more appends the next page, so row 21 IS reachable, and the
 *      footer's count reflects the SERVER's total, not the loaded rows.
 *
 * `/portal/payments` and `/portal/discount-offers` share the exact same loader
 * + footer shape; seeding 21 payments (each needing an invoice) or 21 offers
 * would perturb far more seed state than it proves, so those two are covered
 * by the source-scan guard `src/routes/portal/pagedLists.test.ts` instead.
 *
 * Auth model + seed shape mirror portal.spec.ts: one VendorUser per tenant
 * (`supplier@portal.test`, password "demo"). Rows are seeded with explicit,
 * strictly-decreasing `created_at` values an hour in the future so they sort
 * ahead of every seeded row under the lists' `created_at DESC` ordering —
 * which makes page 1 deterministic without touching any pre-existing data.
 * Every seeded row is removed in a `finally`, and a stale-prefix sweep runs
 * before seeding so a crashed prior run can't skew the totals.
 */

const PORTAL_EMAIL = 'supplier@portal.test';
const PORTAL_PASSWORD = 'demo';

/** Backend `DEFAULT_PAGE_SIZE`, and what the portal asks for per page. */
const PAGE_SIZE = 20;
/** One full page plus a tail — enough that row 21 exists. */
const SEEDED = 25;

test.use({ storageState: { cookies: [], origins: [] } });

async function portalSignIn(page: Page) {
	// Record a cookie-consent choice before first paint. The GDPR banner is
	// position:fixed bottom-centre at z-index 10000, so it sits directly over
	// the Load-more footer and intercepts its clicks — the same reason the
	// shared `signInAndWait` helper does this. The portal has its own sign-in
	// path, which is why it needs its own copy.
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

function countRows(table: string, vendorId: string): number {
	return parseInt(
		tenantPsql(`SELECT count(*) FROM ${table} WHERE vendor_id='${vendorId}'`).trim(),
		10
	);
}

/** Belt-and-braces cleanup: a Playwright *timeout* can abort a test before its
 *  own `finally` runs, and seeded rows left behind would skew every later
 *  count in this shard's tenant. Both prefixes are stable and test-owned. */
test.afterEach(() => {
	tenantPsql(`DELETE FROM purchase_orders WHERE po_number LIKE 'E2E-PAGE-PO-%'`);
	deleteInvoicesWhere(`invoice_number LIKE 'E2E-PAGE-INV-%'`);
});

test.describe('/portal — list pagination', () => {
	test('purchase orders: page 1 is capped, Load more reaches row 21, footer counts the whole set', async ({
		page,
	}) => {
		const { vendorId, orgId } = portalVendor();
		const prefix = `E2E-PAGE-PO-${Date.now()}`;

		// Sweep any leftovers from a crashed prior run before measuring.
		tenantPsql(`DELETE FROM purchase_orders WHERE po_number LIKE 'E2E-PAGE-PO-%'`);

		try {
			tenantPsql(
				`INSERT INTO purchase_orders
				   (id, po_number, vendor_id, total, status, organization_id, created_at, updated_at)
				 SELECT gen_random_uuid(),
				        '${prefix}-' || lpad(i::text, 2, '0'),
				        '${vendorId}', 100 + i, 'open', '${orgId}',
				        now() + interval '1 hour' - (i * interval '1 minute'),
				        now()
				 FROM generate_series(1, ${SEEDED}) AS s(i)`
			);

			const total = countRows('purchase_orders', vendorId);
			expect(total).toBeGreaterThanOrEqual(SEEDED);

			await portalSignIn(page);
			await page.getByRole('link', { name: 'Purchase Orders' }).click();
			await expect(page).toHaveURL(/\/portal\/purchase-orders/, { timeout: 5_000 });

			const rows = page.locator('table tbody tr');
			// The cliff: exactly one page, never the whole set.
			await expect(rows).toHaveCount(PAGE_SIZE, { timeout: 10_000 });

			// Row 21 of the ordering is the 21st seeded PO — not on page 1.
			const row21 = page.locator('table tbody tr', { hasText: `${prefix}-21` });
			await expect(row21).toHaveCount(0);

			// The footer counts the SERVER's total, not the 20 loaded rows, and
			// the end-of-list message must not be showing while rows are missing.
			const loadMore = page.locator('.btn-load-more');
			await expect(loadMore).toHaveText(`Load more (${PAGE_SIZE} of ${total})`);
			await expect(page.locator('.load-more-end')).toHaveCount(0);

			await loadMore.click();

			// Page 2 appended: row 21 is reachable.
			const expectedLoaded = Math.min(PAGE_SIZE * 2, total);
			await expect(rows).toHaveCount(expectedLoaded, { timeout: 10_000 });
			await expect(row21).toHaveCount(1);

			if (expectedLoaded === total) {
				// Everything is loaded — only now may the footer claim so.
				await expect(page.locator('.load-more-end')).toHaveText(
					`Showing all ${total} purchase orders`
				);
				await expect(loadMore).toHaveCount(0);
			} else {
				await expect(loadMore).toHaveText(`Load more (${expectedLoaded} of ${total})`);
			}
		} finally {
			tenantPsql(`DELETE FROM purchase_orders WHERE po_number LIKE '${prefix}-%'`);
		}
	});

	test('invoices: Load more reaches invoice 21 and the footer counts the whole set', async ({
		page,
	}) => {
		const { vendorId, orgId } = portalVendor();
		const prefix = `E2E-PAGE-INV-${Date.now()}`;

		deleteInvoicesWhere(`invoice_number LIKE 'E2E-PAGE-INV-%'`);

		try {
			tenantPsql(
				`INSERT INTO invoices
				   (id, correlation_id, invoice_number, vendor_name, amount, currency, status,
				    organization_id, vendor_id, created_at, updated_at)
				 SELECT gen_random_uuid(), gen_random_uuid(),
				        '${prefix}-' || lpad(i::text, 2, '0'),
				        'E2E Pagination Vendor', 100 + i, 'USD', 'new',
				        '${orgId}', '${vendorId}',
				        now() + interval '1 hour' - (i * interval '1 minute'),
				        now()
				 FROM generate_series(1, ${SEEDED}) AS s(i)`
			);

			const total = countRows('invoices', vendorId);
			expect(total).toBeGreaterThanOrEqual(SEEDED);

			await portalSignIn(page);

			// Only the invoice rows are `.clickable` — an expanded chat panel is a
			// sibling `tr.chat-row`, so this count can't drift with the chat UI.
			const rows = page.locator('table tbody tr.clickable');
			await expect(rows).toHaveCount(PAGE_SIZE, { timeout: 10_000 });

			const invoice21 = page.locator('table tbody tr.clickable', {
				hasText: `${prefix}-21`,
			});
			await expect(invoice21).toHaveCount(0);

			const loadMore = page.locator('.btn-load-more');
			await expect(loadMore).toHaveText(`Load more (${PAGE_SIZE} of ${total})`);

			await loadMore.click();

			const expectedLoaded = Math.min(PAGE_SIZE * 2, total);
			await expect(rows).toHaveCount(expectedLoaded, { timeout: 10_000 });
			await expect(invoice21).toHaveCount(1);

			if (expectedLoaded === total) {
				await expect(page.locator('.load-more-end')).toHaveText(`Showing all ${total} invoices`);
			}
		} finally {
			deleteInvoicesWhere(`invoice_number LIKE '${prefix}-%'`);
		}
	});
});
