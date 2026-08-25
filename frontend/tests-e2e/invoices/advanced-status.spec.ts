import { expect, test } from '../fixtures/helpers';

/**
 * Advanced Search status filter ↔ inline chips share ONE selection.
 *
 * The inline chip row shows a quick, high-traffic subset (New / Ready for
 * Review / Approved / Failed). The Advanced Search modal offers the FULL
 * status set. Selecting a status that's only in the modal must (a) filter
 * the table and (b) surface as an active inline chip — and the two controls
 * must never clobber each other's selection (the old `buildParams` bug, where
 * the modal's `status=` silently overwrote the chip's).
 *
 * Seed (`seed_tenant`) gives every e2e tenant invoices in new, pending,
 * ready_for_review, approved, posted_in_erp — so `Posted in ERP` is a real
 * modal-only status with rows behind it.
 */

test.describe('/invoices advanced status filter', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/invoices');
		await page.waitForLoadState('networkidle');
	});

	test('transient/terminal statuses are not in the quick chip row', async ({ page }) => {
		// Extracting (pending) and Posted in ERP live in the modal now, not
		// inline — even though the seed has invoices in those states.
		await expect(page.locator('.filter-chip', { hasText: /^Extracting/ })).toHaveCount(0);
		await expect(page.locator('.filter-chip', { hasText: /^Posted in ERP/ })).toHaveCount(0);
		// The actionable subset is present.
		await expect(page.locator('.filter-chip', { hasText: /^New\s/ })).toBeVisible();
	});

	test('a modal-only status filters the table and surfaces as an active chip', async ({ page }) => {
		await page.locator('.advanced-btn').click();
		const modal = page.getByRole('dialog', { name: 'Advanced search' });
		await expect(modal).toBeVisible();

		const filtered = page.waitForResponse(
			(res) => res.url().includes('/api/invoices') && res.url().includes('status=posted_in_erp')
		);
		await modal.locator('.status-chip', { hasText: /^Posted in ERP$/ }).click();
		await modal.locator('.btn-apply').click();
		await filtered;

		// Surfaces as an active inline chip — an active filter is never invisible.
		const chip = page.locator('.filter-chip', { hasText: /^Posted in ERP/ });
		await expect(chip).toBeVisible();
		await expect(chip).toHaveClass(/active/);

		// Table narrowed to posted_in_erp (status badge is third-to-last column —
		// Assigned To then Actions follow it).
		const badges = await page.locator('table tbody tr td:nth-last-child(3)').allTextContents();
		expect(badges.length).toBeGreaterThan(0);
		expect(badges.every((b) => b.toLowerCase().includes('posted'))).toBe(true);
	});

	test('inline chip selection is reflected in the modal (shared state, no clobber)', async ({
		page,
	}) => {
		// Pick Approved on an inline chip.
		const filtered = page.waitForResponse(
			(res) => res.url().includes('/api/invoices') && res.url().includes('status=approved')
		);
		await page.locator('.filter-chip', { hasText: /^Approved\s/ }).click();
		await filtered;

		// Open the modal — Approved must already read as selected there (the open
		// handler seeds the modal from the live selection), proving one source of
		// truth rather than two filters that fight.
		await page.locator('.advanced-btn').click();
		const modal = page.getByRole('dialog', { name: 'Advanced search' });
		await expect(modal.locator('.status-chip.selected', { hasText: /^Approved$/ })).toBeVisible();
	});
});
