import { expect, signInAndWait, test } from '../fixtures/helpers';

/**
 * /admin — admin-only user management. Seed creates 4 users per tenant
 * (admin, manager, clerk, cfo) so the table renders 4 rows.
 */

test.describe('/admin', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/admin');
		await page.waitForLoadState('networkidle');
	});

	test('lists the seeded users', async ({ page, tenantAdmin }) => {
		await expect(page.getByRole('heading', { name: 'Users & Roles' })).toBeVisible();
		await expect(page.locator('table tbody tr').first()).toBeVisible();
		// 4 users in seed per tenant.
		expect(await page.locator('table tbody tr').count()).toBeGreaterThanOrEqual(4);

		// Each row's email cell should match what the seed uses.
		const emails = await page.locator('table tbody td.email-cell').allTextContents();
		expect(emails).toContain(tenantAdmin.email);
	});

	test('the current user is marked with "You"', async ({ page, tenantAdmin }) => {
		// Storage state authed us as the current worker's admin.
		const youRow = page.locator('table tbody tr', { hasText: tenantAdmin.email });
		await expect(youRow.locator('.you-badge')).toBeVisible();
	});

	test('Invite User opens the create-user modal', async ({ page }) => {
		await page.getByRole('button', { name: '+ Invite User' }).click();
		const modal = page.locator('div.modal[role="dialog"][aria-label="Invite user"]');
		await expect(modal).toBeVisible({ timeout: 5_000 });
		await expect(modal.getByRole('heading', { name: 'Invite User' })).toBeVisible();
	});

	test('Roles section tab switches content and updates the URL', async ({ page }) => {
		// Users + Roles are peer tabs in the sidebar's Settings section bar now,
		// not a second tab row inside the page.
		await page.locator('.section-tabs a.section-tab', { hasText: 'Roles' }).click();
		await expect(page).toHaveURL(/\/admin\?tab=roles$/);
		await expect(page.getByRole('heading', { name: 'System roles' })).toBeVisible();
		await expect(page.getByRole('button', { name: '+ Create Role' })).toBeVisible();
		// The Invite-User action belongs to the Users tab only.
		await expect(page.getByRole('button', { name: '+ Invite User' })).toHaveCount(0);
	});

	test('/admin/roles redirects to the Roles tab (back-compat)', async ({ page }) => {
		await page.goto('/admin/roles');
		await expect(page).toHaveURL(/\/admin\?tab=roles$/);
		await expect(page.getByRole('heading', { name: 'Custom roles' })).toBeVisible();
	});
});

test.describe('/admin (clerk — not authorized)', () => {
	test.use({ storageState: { cookies: [], origins: [] } });

	test('ap_clerk is redirected away with no uncaught error, and the API 403s them', async ({
		page,
		tenantClerk
	}) => {
		const pageErrors: string[] = [];
		page.on('pageerror', (err) => pageErrors.push(err.message));

		await signInAndWait(page, tenantClerk);

		await page.goto('/admin');
		// admin-only — the page waits for /me then bounces the clerk to root,
		// same guard as the sibling admin-only pages (/admin/api-keys,
		// /admin/partner, /admin/webhooks). Regression: the panels used to
		// mount unconditionally and their unguarded fetch threw an uncaught
		// exception on the guaranteed 403 instead of redirecting cleanly.
		await page.waitForURL(/^http:\/\/[^/]+:7777\/?$/, { timeout: 15_000 });
		await expect(page.getByRole('heading', { name: 'Users & Roles' })).toHaveCount(0);
		expect(pageErrors).toEqual([]);
	});
});
