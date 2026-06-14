import { expect, signInAndWait, test } from '../fixtures/helpers';

// Start unauthenticated — every test signs in as a specific role to assert sidebar gating.
test.use({ storageState: { cookies: [], origins: [] } });

/**
 * RBAC — sidebar nav visibility per role.
 *
 * Sidebar config (frontend/src/lib/components/Sidebar.svelte) gates
 * each nav item via `requiredRoles`. The matrix below is the contract;
 * a regression that loosens any cell could expose admin-only surfaces
 * to a non-admin user.
 *
 * | Item            | clerk | manager | cfo | admin |
 * |-----------------|-------|---------|-----|-------|
 * | Dashboard       |  ✓    |   ✓    |  ✓  |  ✓    |
 * | Notifications   |  ✓    |   ✓    |  ✓  |  ✓    |
 * | Invoices        |  ✓    |   ✓    |  ✓  |  ✓    |
 * | Credit Memos    |       |   ✓    |  ✓  |  ✓    |
 * | Contracts       |  ✓    |   ✓    |  ✓  |  ✓    |
 * | Expenses        |  ✓    |   ✓    |  ✓  |  ✓    |
 * | Payments        |       |   ✓    |  ✓  |  ✓    |
 * | Vendors         |       |   ✓    |  ✓  |  ✓    |
 * | Purchase Orders |       |   ✓    |  ✓  |  ✓    |
 * | Goods Receipts  |       |   ✓    |  ✓  |  ✓    |
 * | Requisitions    |  ✓    |   ✓    |  ✓  |  ✓    |
 * | Intake          |  ✓    |   ✓    |  ✓  |  ✓    |
 * | Catalogs        |  ✓    |   ✓    |  ✓  |  ✓    |
 * | Budgets         |       |   ✓    |  ✓  |  ✓    |
 * | Cash Flow       |       |        |  ✓  |  ✓    |
 * | 1099 Reporting  |       |   ✓    |  ✓  |  ✓    |
 * | Exceptions      |       |   ✓    |     |  ✓    |
 * | Workflows       |       |        |     |  ✓    |
 * | Audit Trail     |       |        |  ✓  |  ✓    |
 * | Organization    |       |        |     |  ✓    |
 * | Users           |       |        |     |  ✓    |
 * | Roles           |       |        |     |  ✓    |
 *
 * Each row mirrors the matching backend read-RBAC gate. Every
 * clerk-visible item (Contracts/Expenses/Requisitions/Intake/Catalogs)
 * has a list endpoint that allows ap_clerk to read — a clerk browses
 * Catalogs + guided buying to raise a requisition, whose line items
 * carry a catalog_item_id.
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
	test('clerk: Dashboard, Notifications, Invoices, Contracts, Expenses, Requisitions, Intake, Catalogs', async ({
		page,
		tenantClerk
	}) => {
		await signInAndWait(page, tenantClerk);
		await assertSidebarLinks(page, [
			'/',
			'/notifications',
			'/invoices',
			'/contracts',
			'/expenses',
			'/requisitions',
			'/intake',
			'/catalogs'
		]);
	});

	test('manager: + Credit Memos, Payments, Vendors, POs, GRs, Catalogs, Budgets, 1099, Exceptions', async ({
		page,
		tenantManager
	}) => {
		await signInAndWait(page, tenantManager);
		await assertSidebarLinks(page, [
			'/',
			'/notifications',
			'/invoices',
			'/credit-memos',
			'/contracts',
			'/expenses',
			'/payments',
			'/vendors',
			'/purchase-orders',
			'/goods-receipts',
			'/requisitions',
			'/intake',
			'/catalogs',
			'/budgets',
			'/tax',
			'/exceptions'
		]);
	});

	test('cfo: manager set minus Exceptions, plus Cash Flow + Audit Trail (no Exceptions, no Users)', async ({
		page,
		tenantCfo
	}) => {
		await signInAndWait(page, tenantCfo);
		await assertSidebarLinks(page, [
			'/',
			'/notifications',
			'/invoices',
			'/credit-memos',
			'/contracts',
			'/expenses',
			'/payments',
			'/vendors',
			'/purchase-orders',
			'/goods-receipts',
			'/requisitions',
			'/intake',
			'/catalogs',
			'/budgets',
			'/cfo',
			'/tax',
			'/audit'
		]);
	});
});
