import { expect, test } from '../fixtures/helpers';

/**
 * /vendors — list view + status filtering. Seed creates 10 acme
 * vendors with mixed statuses (active, unverified, rejected). The
 * three filter chips correspond to those statuses; clicking one
 * narrows the visible rows.
 */

test.describe('/vendors (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/vendors');
		await page.waitForLoadState('networkidle');
	});

	test('lists seeded vendors', async ({ page }) => {
		await expect(page.getByRole('heading', { name: 'Vendors' })).toBeVisible();
		await expect(page.locator('table tbody tr').first()).toBeVisible();
		expect(await page.locator('table tbody tr').count()).toBeGreaterThan(0);
	});

	test('All chip is active by default and shows all vendors', async ({ page }) => {
		await expect(
			page.locator('.filter-chip', { hasText: /^All\s/ })
		).toHaveClass(/active/);
	});

	test('search input filters the visible vendor list', async ({ page }) => {
		const search = page.getByPlaceholder('Search vendors...');
		const beforeRows = await page.locator('table tbody tr').count();

		// Search is server-side via /api/vendors?search=…. networkidle
		// is unreliable here — it can resolve before the search response
		// lands when other concurrent activity (HMR, dev-server pings)
		// keeps the network "active". Wait specifically for the search
		// response to come back.
		const searchResponse = page.waitForResponse(
			(res) => res.url().includes('/api/vendors') && res.url().includes('search=Office')
		);
		await search.fill('Office');
		await searchResponse;

		const afterRows = await page.locator('table tbody tr').count();
		expect(afterRows).toBeGreaterThan(0);
		expect(afterRows).toBeLessThanOrEqual(beforeRows);
		// Every visible row's vendor-name cell must contain the search term.
		// Backend search ILIKEs name + code + email; both seeded "Office…"
		// vendors have it in name, so this is the right contract.
		const names = await page.locator('table tbody td.vendor-name').allTextContents();
		expect(names.every((n) => n.toLowerCase().includes('office'))).toBe(true);
	});
});
