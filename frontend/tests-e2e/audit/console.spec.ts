import { expect, signInAndWait, test } from '../fixtures/helpers';

/**
 * Auditor console (/audit) — SOX audit-trail export surface.
 *
 * Admin (default storage-state) runs a date-range query, sees rows, and
 * downloads the CSV. A clerk who hand-types the URL is bounced (RBAC parity
 * with the backend, which 403s the export for non-admin/CFO).
 *
 * Waits are on real signals (the /api/audit/export response, the download
 * event) — never a fixed timeout.
 */

test.describe('audit console (admin)', () => {
	test('runs a date-range query and sees rows', async ({ page }) => {
		await page.goto('/audit');
		await expect(page.locator('h1', { hasText: 'Audit Trail' })).toBeVisible();

		const exported = page.waitForResponse(
			(r) => r.url().includes('/api/audit/export') && r.request().method() === 'GET'
		);
		await page.getByRole('button', { name: 'Run query' }).click();
		const resp = await exported;
		expect(resp.status()).toBe(200);

		// The seeded tenant has invoice/payment activity, so the trail is
		// non-empty. The table renders one row per audit entry.
		await expect(page.locator('.grid-container tbody tr').first()).toBeVisible();
	});

	test('downloads the CSV export', async ({ page }) => {
		await page.goto('/audit');

		const exported = page.waitForResponse((r) => r.url().includes('/api/audit/export'));
		await page.getByRole('button', { name: 'Run query' }).click();
		await exported;
		await expect(page.locator('.grid-container tbody tr').first()).toBeVisible();

		const download = page.waitForEvent('download');
		await page.getByRole('button', { name: 'Download CSV' }).click();
		const file = await download;
		expect(file.suggestedFilename()).toMatch(/^audit_export_.*\.csv$/);
	});

	test('by-invoice mode requires an id before querying', async ({ page }) => {
		await page.goto('/audit');
		await page.getByRole('tab', { name: 'By invoice' }).click();
		await page.getByRole('button', { name: 'Run query' }).click();
		await expect(page.getByRole('alert')).toContainText('invoice ID');
	});
});

test.describe('audit console (clerk RBAC)', () => {
	// Start unauthenticated, sign in as the clerk explicitly.
	test.use({ storageState: { cookies: [], origins: [] } });

	test('clerk sees access-denied, not the console', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);
		await page.goto('/audit');
		// RBAC parity with the backend (403 for non-admin/CFO): the page renders
		// an access-denied panel and never the query controls. Asserting on the
		// denial (not a redirect) avoids racing the async role load.
		await expect(page.locator('.audit-denied')).toBeVisible();
		await expect(page.getByRole('button', { name: 'Run query' })).toHaveCount(0);
		// The clerk also can't see the Audit Trail nav item.
		await expect(page.locator('aside.sidebar a[href="/audit"]')).toHaveCount(0);
	});
});
