import { expect, signInAndWait, test } from '../fixtures/helpers';

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
	test('clerk: only Dashboard + Invoices', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);
		await assertSidebarLinks(page, ['/', '/invoices']);
	});

	test('manager: Dashboard, Invoices, Credit Memos, Payments, Vendors, POs, GRs, Exceptions', async ({
		page,
		tenantManager
	}) => {
		await signInAndWait(page, tenantManager);
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
		page,
		tenantCfo
	}) => {
		await signInAndWait(page, tenantCfo);
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
