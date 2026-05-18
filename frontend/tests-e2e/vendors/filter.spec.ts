import { expect, test } from '../fixtures/helpers';

import { signInAndWait } from '../fixtures/helpers';

/**
 * /vendors status-chip filtering. Seed has at least one vendor in
 * each of (active, unverified, rejected). Clicking a chip narrows the
 * table to the matching status; clicking All restores everything.
 */

test.describe('/vendors status filter (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
		await page.goto('/vendors');
		await page.waitForLoadState('networkidle');
	});

	test('Unverified chip narrows to vendors in unverified status', async ({ page }) => {
		const beforeRows = await page.locator('table tbody tr').count();

		const filtered = page.waitForResponse(
			(r) => r.url().includes('/api/vendors') && r.url().includes('status=unverified')
		);
		await page.locator('.filter-chip', { hasText: /^Unverified/ }).click();
		await filtered;

		await expect(page.locator('.filter-chip', { hasText: /^Unverified/ })).toHaveClass(
			/active/
		);
		const after = await page.locator('table tbody tr').count();
		expect(after).toBeGreaterThan(0);
		expect(after).toBeLessThanOrEqual(beforeRows);
		// Every visible row carries the .unverified class on the <tr>.
		const rows = page.locator('table tbody tr');
		const total = await rows.count();
		for (let i = 0; i < total; i++) {
			await expect(rows.nth(i)).toHaveClass(/unverified/);
		}
	});

	test('Active chip narrows to vendors in active status', async ({ page }) => {
		const filtered = page.waitForResponse(
			(r) => r.url().includes('/api/vendors') && r.url().includes('status=active')
		);
		await page.locator('.filter-chip', { hasText: /^Active$/ }).click();
		await filtered;

		await expect(page.locator('.filter-chip', { hasText: /^Active$/ })).toHaveClass(
			/active/
		);
		// Active rows have neither .unverified nor .rejected.
		const rows = page.locator('table tbody tr');
		expect(await rows.count()).toBeGreaterThan(0);
		const first = rows.first();
		await expect(first).not.toHaveClass(/unverified/);
		await expect(first).not.toHaveClass(/rejected/);
	});

	test('Rejected chip narrows to rejected vendors', async ({ page }) => {
		const filtered = page.waitForResponse(
			(r) => r.url().includes('/api/vendors') && r.url().includes('status=rejected')
		);
		await page.locator('.filter-chip', { hasText: /^Rejected$/ }).click();
		await filtered;

		const rows = page.locator('table tbody tr');
		expect(await rows.count()).toBeGreaterThan(0);
		await expect(rows.first()).toHaveClass(/rejected/);
	});

	test('All chip restores the unfiltered list', async ({ page }) => {
		// Narrow first — wait for the filtered fetch.
		const narrowed = page.waitForResponse(
			(r) => r.url().includes('/api/vendors') && r.url().includes('status=unverified')
		);
		await page.locator('.filter-chip', { hasText: /^Unverified/ }).click();
		await narrowed;

		const restored = page.waitForResponse(
			(r) => r.url().includes('/api/vendors') && !r.url().includes('status=')
		);
		await page.locator('.filter-chip', { hasText: /^All\s/ }).click();
		await restored;

		await expect(page.locator('.filter-chip', { hasText: /^All\s/ })).toHaveClass(
			/active/
		);
	});
});
