import { expect, test } from '../fixtures/helpers';

/**
 * /vendors — sanctions-screening + risk surface. Every vendor row carries a
 * screening pill (driven by VendorResponse.screening_status; defaults to
 * "Unscreened" so the badge always renders regardless of seed state), and
 * clicking a row opens the detail modal with the "Screening & Risk" panel and
 * the screening-history timeline.
 */

test.describe('/vendors screening & risk (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/vendors');
		await page.waitForLoadState('networkidle');
	});

	test('list shows a Screening column with a status pill per row', async ({ page }) => {
		await expect(page.getByRole('columnheader', { name: 'Screening' })).toBeVisible();
		// First row renders at least one screening pill (defaults to Unscreened).
		const firstRow = page.locator('table tbody tr').first();
		await expect(firstRow.locator('.screen-badge').first()).toBeVisible();
	});

	test('clicking a vendor opens the Screening & Risk detail modal', async ({ page }) => {
		const firstRow = page.locator('table tbody tr').first();
		await firstRow.locator('td.vendor-name .row-link').click();

		const modal = page.getByRole('dialog', { name: 'Vendor screening and risk' });
		await expect(modal).toBeVisible();
		await expect(modal.getByRole('heading', { name: 'Screening & Risk' })).toBeVisible();
		await expect(modal.getByText('Screening status')).toBeVisible();
		await expect(modal.getByText('Risk level')).toBeVisible();
		// History section renders (timeline rows, an empty note, or "Loading…").
		await expect(modal.getByRole('heading', { name: 'Screening history' })).toBeVisible();

		// Admin can mutate — the re-screen action is present and role-gated.
		await expect(modal.getByRole('button', { name: 'Re-screen now' })).toBeVisible();
		await expect(modal.getByRole('button', { name: 'Recompute risk' })).toBeVisible();
	});
});
