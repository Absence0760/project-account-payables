import { expect, test } from '../fixtures/helpers';

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

	test('Roles tab switches content and updates the URL', async ({ page }) => {
		await page.getByRole('tab', { name: 'Roles' }).click();
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
