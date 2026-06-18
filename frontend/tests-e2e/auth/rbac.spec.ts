import { expect, signInAndWait, test } from '../fixtures/helpers';
import type { Page } from '@playwright/test';

/**
 * RBAC — sidebar + section-tab visibility per role.
 *
 * The primary nav (`frontend/src/lib/nav.ts`) keeps high-traffic destinations as
 * top-level links and folds the rest into groups (Procurement / Billing /
 * Insights / Settings). The sidebar shows ONE row per group, pointing at the
 * first child the role can see; each grouped page then renders the group's
 * children as a section sub-tab bar — again RBAC-filtered. Both layers read the
 * same per-route `roles` gate, so each test asserts both:
 *   1. the sidebar rows a role sees (direct links + group landings), and
 *   2. the section tabs a role sees inside a group.
 *
 * Login budget: the backend caps logins at 10/60s per IP and every e2e login
 * shares localhost, so we sign in ONCE per non-admin role (asserting everything
 * for that role in the single session) and cover admin on the cached default
 * storage-state — no extra fresh login. Admin route reachability is also in
 * `smoke/nav.spec.ts`.
 *
 * Per-route gates (mirroring the backend read-RBAC):
 *   Direct: Dashboard(all) · Invoices(all) · Payments(adm/mgr/cfo) ·
 *           Vendors(adm/mgr/cfo) · Exceptions(adm/mgr)
 *   Procurement: PurchaseOrders·GoodsReceipts·Budgets(adm/mgr/cfo);
 *                Requisitions·Intake·Catalogs(all)
 *   Billing: Contracts·Expenses(all); CreditMemos·Discounts(adm/mgr/cfo)
 *   Insights: AIAssistant(all); CashFlow(adm/cfo); 1099(adm/mgr/cfo)
 *   Settings: Organization·Users·Roles·Workflows(admin); AuditTrail(adm/cfo)
 */

async function sidebarHrefs(page: Page): Promise<string[]> {
	const links = page.locator('aside.sidebar a.nav-item');
	return (
		await links.evaluateAll((els) =>
			els.map((e) => (e as HTMLAnchorElement).getAttribute('href') ?? '')
		)
	).sort();
}

async function sectionTabHrefs(page: Page, route: string): Promise<string[]> {
	await page.goto(route);
	await expect(page.locator('aside.sidebar')).toBeVisible();
	return (
		await page
			.locator('.section-tabs a.section-tab')
			.evaluateAll((els) => els.map((e) => (e as HTMLAnchorElement).getAttribute('href') ?? ''))
	).sort();
}

test.describe('RBAC — non-admin roles (one fresh sign-in each)', () => {
	test.use({ storageState: { cookies: [], origins: [] } });

	test('clerk: Dashboard/Invoices + folded group landings; group tabs RBAC-filtered', async ({
		page,
		tenantClerk
	}) => {
		await signInAndWait(page, tenantClerk);
		// A group row links to the FIRST child the role can see.
		expect(await sidebarHrefs(page)).toEqual(
			['/', '/invoices', '/requisitions', '/contracts', '/assistant'].sort()
		);
		// Procurement tabs: only the all-roles children.
		expect(await sectionTabHrefs(page, '/requisitions')).toEqual(
			['/requisitions', '/intake', '/catalogs'].sort()
		);
		// Billing tabs: no Credit Memos / Discounts for a clerk.
		expect(await sectionTabHrefs(page, '/contracts')).toEqual(['/contracts', '/expenses'].sort());
	});

	test('manager: + Payments/Vendors/Exceptions; full Procurement tabs; no Settings', async ({
		page,
		tenantManager
	}) => {
		await signInAndWait(page, tenantManager);
		expect(await sidebarHrefs(page)).toEqual(
			['/', '/invoices', '/payments', '/vendors', '/exceptions', '/purchase-orders', '/contracts', '/assistant'].sort()
		);
		expect(await sectionTabHrefs(page, '/purchase-orders')).toEqual(
			['/purchase-orders', '/goods-receipts', '/requisitions', '/intake', '/catalogs', '/budgets'].sort()
		);
	});

	test('cfo: gains Settings (Audit landing); lone Settings tab suppresses the bar', async ({
		page,
		tenantCfo
	}) => {
		await signInAndWait(page, tenantCfo);
		expect(await sidebarHrefs(page)).toEqual(
			['/', '/invoices', '/payments', '/vendors', '/purchase-orders', '/contracts', '/assistant', '/audit'].sort()
		);
		// Only Audit Trail is cfo-visible in Settings → no section bar (a lone tab
		// would just duplicate the page title).
		await page.goto('/audit');
		await expect(page.locator('aside.sidebar')).toBeVisible();
		await expect(page.locator('.section-tabs')).toHaveCount(0);
	});
});

test.describe('RBAC — admin (cached session, no extra login)', () => {
	test('full sidebar set + Settings tabs; no bar on a top-level route', async ({ page }) => {
		await page.goto('/');
		await expect(page.locator('aside.sidebar')).toBeVisible();
		expect(await sidebarHrefs(page)).toEqual(
			['/', '/invoices', '/payments', '/vendors', '/exceptions', '/purchase-orders', '/contracts', '/assistant', '/organization'].sort()
		);
		expect(await sectionTabHrefs(page, '/organization')).toEqual(
			['/organization', '/admin?tab=users', '/admin?tab=roles', '/audit', '/workflows'].sort()
		);
		// Direct (non-grouped) routes have no section bar.
		await page.goto('/invoices');
		await expect(page.locator('.section-tabs')).toHaveCount(0);
	});
});
