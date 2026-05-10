import { expect, test } from '@playwright/test';

import { ACME_CFO, ACME_CLERK, ACME_MANAGER, signInAndWait } from '../fixtures/helpers';

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
 * | Exceptions   |       |   ✓    |     |  ✓    |
 * | Workflows    |       |        |     |  ✓    |
 * | Organization |       |        |     |  ✓    |
 * | Admin        |       |        |     |  ✓    |
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

	test('manager: Dashboard, Invoices, Credit Memos, Payments, Vendors, Exceptions', async ({
		page
	}) => {
		await signInAndWait(page, ACME_MANAGER);
		await assertSidebarLinks(page, [
			'/',
			'/invoices',
			'/credit-memos',
			'/payments',
			'/vendors',
			'/exceptions'
		]);
	});

	test('cfo: Dashboard, Invoices, Credit Memos, Payments, Vendors (no Exceptions, no Admin)', async ({
		page
	}) => {
		await signInAndWait(page, ACME_CFO);
		await assertSidebarLinks(page, [
			'/',
			'/invoices',
			'/credit-memos',
			'/payments',
			'/vendors'
		]);
	});
});
