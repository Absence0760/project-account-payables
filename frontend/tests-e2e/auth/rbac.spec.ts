import { expect, test, ACME_BASE } from '../fixtures/helpers';

import { ACME_CFO, ACME_CLERK, ACME_MANAGER, signInAndWait } from '../fixtures/helpers';

// Pinned to the acme tenant: this spec uses ACME_*/TECHFLOW_* creds or
// asserts cross-tenant isolation that requires fixed tenant slugs. The
// per-worker baseURL from fixtures/helpers.ts would otherwise route to
// the wrong tenant. Multiple workers may share acme here — keep this
// file's tests read-only or idempotent.
test.use({ baseURL: ACME_BASE });

/**
 * RBAC — sidebar nav visibility per role.
 *
 * Sidebar config (frontend/src/lib/components/Sidebar.svelte) gates
 * each nav item via `requiredRoles`. The matrix below is the contract;
 * a regression that loosens any cell could expose admin-only surfaces
 * to a non-admin user.
 *
 * | Item         | clerk | manager | cfo | admin |
 * |--------------|-------|---------|-----|-------|
 * | Dashboard    |  ✓    |   ✓    |  ✓  |  ✓    |
 * | Invoices     |  ✓    |   ✓    |  ✓  |  ✓    |
 * | Credit Memos |       |   ✓    |  ✓  |  ✓    |
 * | Payments     |       |   ✓    |  ✓  |  ✓    |
 * | Vendors      |       |   ✓    |  ✓  |  ✓    |
 * | Purchase Orders |    |   ✓    |  ✓  |  ✓    |
 * | Goods Receipts |     |   ✓    |  ✓  |  ✓    |
 * | Exceptions   |       |   ✓    |     |  ✓    |
 * | Workflows    |       |        |     |  ✓    |
 * | Organization |       |        |     |  ✓    |
 * | Users        |       |        |     |  ✓    |
 *
 * Admin coverage is implicit in nav.spec.ts (admin reaches every
 * route). Here we focus on the non-admin gates.
 */

async function assertSidebarLinks(page: import('@playwright/test').Page, expected: string[]) {
	// All visible nav-item anchors in the sidebar, by their href.
	const links = page.locator('aside.sidebar a.nav-item');
	const count = await links.count();
	const hrefs: string[] = [];
	for (let i = 0; i < count; i++) {
		const href = await links.nth(i).getAttribute('href');
		if (href) hrefs.push(href);
	}
	expect(hrefs.sort()).toEqual(expected.sort());
}

test.describe('RBAC — sidebar visibility', () => {
	test('clerk: only Dashboard + Invoices', async ({ page }) => {
		await signInAndWait(page, ACME_CLERK);
		await assertSidebarLinks(page, ['/', '/invoices']);
	});

	test('manager: Dashboard, Invoices, Credit Memos, Payments, Vendors, POs, GRs, Exceptions', async ({
		page
	}) => {
		await signInAndWait(page, ACME_MANAGER);
		await assertSidebarLinks(page, [
			'/',
			'/invoices',
			'/credit-memos',
			'/payments',
			'/vendors',
			'/purchase-orders',
			'/goods-receipts',
			'/exceptions'
		]);
	});

	test('cfo: Dashboard, Invoices, Credit Memos, Payments, Vendors, POs, GRs (no Exceptions, no Users)', async ({
		page
	}) => {
		await signInAndWait(page, ACME_CFO);
		await assertSidebarLinks(page, [
			'/',
			'/invoices',
			'/credit-memos',
			'/payments',
			'/vendors',
			'/purchase-orders',
			'/goods-receipts'
		]);
	});
});
