import { expect, test } from '../fixtures/helpers';

/**
 * /vendors column sort (issue #328, power-user/Medium) — see
 * invoices/sort.spec.ts for the full rationale. Uses the seed's existing
 * vendors; only asserts the request/URL contract.
 */

test.describe('/vendors column sort (acme admin)', () => {
	test('clicking Vendor sorts ascending, then descending on a second click', async ({
		page
	}) => {
		await page.goto('/vendors');
		await expect(page.locator('table tbody tr').first()).toBeVisible();

		const ascRequest = page.waitForResponse(
			(r) =>
				r.url().includes('/api/vendors?') &&
				r.url().includes('sort=name') &&
				r.url().includes('order=asc')
		);
		await page.getByRole('button', { name: 'Vendor' }).click();
		await ascRequest;
		await expect(page).toHaveURL(/sort=name/);
		await expect(page).toHaveURL(/order=asc/);

		const descRequest = page.waitForResponse(
			(r) =>
				r.url().includes('/api/vendors?') &&
				r.url().includes('sort=name') &&
				r.url().includes('order=desc')
		);
		await page.getByRole('button', { name: 'Vendor' }).click();
		await descRequest;
		await expect(page).toHaveURL(/order=desc/);
	});

	test('switching to Status starts it ascending', async ({ page }) => {
		await page.goto('/vendors');
		await expect(page.locator('table tbody tr').first()).toBeVisible();

		await page.getByRole('button', { name: 'Vendor' }).click();
		await page.waitForResponse((r) => r.url().includes('sort=name'));

		const statusAsc = page.waitForResponse(
			(r) =>
				r.url().includes('/api/vendors?') &&
				r.url().includes('sort=status') &&
				r.url().includes('order=asc')
		);
		await page.getByRole('button', { name: 'Status', exact: true }).click();
		await statusAsc;
		await expect(page).toHaveURL(/sort=status/);
	});
});
