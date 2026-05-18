import { expect, test, ACME_BASE } from '../fixtures/helpers';

import { signInAndWait } from '../fixtures/helpers';

// Pinned to the acme tenant: this spec uses ACME_*/TECHFLOW_* creds or
// asserts cross-tenant isolation that requires fixed tenant slugs. The
// per-worker baseURL from fixtures/helpers.ts would otherwise route to
// the wrong tenant. Multiple workers may share acme here — keep this
// file's tests read-only or idempotent.
test.use({ baseURL: ACME_BASE });

/**
 * /admin — admin-only user management. Seed creates 4 acme users
 * (admin, manager, clerk, cfo) so the table renders 4 rows.
 */

test.describe('/admin (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
		await page.goto('/admin');
		await page.waitForLoadState('networkidle');
	});

	test('lists the seeded users', async ({ page }) => {
		await expect(page.getByRole('heading', { name: 'Users' })).toBeVisible();
		await expect(page.locator('table tbody tr').first()).toBeVisible();
		// 4 acme users in seed.
		expect(await page.locator('table tbody tr').count()).toBeGreaterThanOrEqual(4);

		// Each row's email cell should match what the seed uses.
		const emails = await page.locator('table tbody td.email-cell').allTextContents();
		expect(emails).toContain('demo@acme.com');
	});

	test('the current user is marked with "You"', async ({ page }) => {
		// `signInAndWait` defaults to ACME_ADMIN (demo@acme.com).
		const youRow = page.locator('table tbody tr', { hasText: 'demo@acme.com' });
		await expect(youRow.locator('.you-badge')).toBeVisible();
	});

	test('Invite User opens the create-user modal', async ({ page }) => {
		await page.getByRole('button', { name: '+ Invite User' }).click();
		const modal = page.locator('div.modal[role="dialog"][aria-label="Invite user"]');
		await expect(modal).toBeVisible({ timeout: 5_000 });
		await expect(modal.getByRole('heading', { name: 'Invite User' })).toBeVisible();
	});
});
