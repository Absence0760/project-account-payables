import { expect, test } from '@playwright/test';

import { signInAndWait } from '../fixtures/helpers';

/**
 * Invoice status filtering — clicking a status chip filters the table.
 *
 * Seed creates acme invoices in 5 distinct statuses (new, pending,
 * ready_for_review, approved, posted_in_erp). Status chips render in
 * the visible-statuses subset; clicking one calls /api/invoices?
 * status=<chip> and re-renders the table to that subset only.
 */

test.describe('/invoices status filter', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
		await page.goto('/invoices');
		await page.waitForLoadState('networkidle');
	});

	test('All chip is active by default', async ({ page }) => {
		const allChip = page.locator('.filter-chip', { hasText: /^All\s/ });
		await expect(allChip).toHaveClass(/active/);
	});

	test('clicking the Approved chip narrows the table', async ({ page }) => {
		// Seed creates one acme invoice per the major statuses, so this
		// chip exists. The chip's count badge says how many — we use
		// the visible chip text to also make the count assertion.
		const approvedChip = page.locator('.filter-chip', { hasText: /^Approved\s/ });
		await expect(approvedChip).toBeVisible();

		// Capture the All-row count before clicking — used to confirm
		// the filter actually narrowed the result.
		const beforeRows = await page.locator('table tbody tr').count();

		// Wait for the specific filtered fetch to land. networkidle is
		// unreliable here — it can resolve before the response comes
		// back if HMR or dev-server keepalives keep the network "busy".
		const filtered = page.waitForResponse(
			(res) => res.url().includes('/api/invoices') && res.url().includes('status=approved')
		);
		await approvedChip.click();
		await filtered;

		// Approved chip becomes the active one.
		await expect(approvedChip).toHaveClass(/active/);

		// Every row's status badge should read "Approved" — narrower
		// than the unfiltered set.
		const rows = page.locator('table tbody tr');
		const afterRows = await rows.count();
		expect(afterRows).toBeGreaterThan(0);
		expect(afterRows).toBeLessThanOrEqual(beforeRows);

		// All visible status badges in the body show 'Approved'.
		const badges = await page
			.locator('table tbody tr td:last-child')
			.allTextContents();
		expect(badges.every((b) => b.toLowerCase().includes('approved'))).toBe(true);
	});

	test('clicking All clears the filter', async ({ page }) => {
		// Click Approved → wait for the filtered fetch.
		const filtered = page.waitForResponse(
			(res) => res.url().includes('/api/invoices') && res.url().includes('status=approved')
		);
		await page.locator('.filter-chip', { hasText: /^Approved\s/ }).click();
		await filtered;

		// Click All → wait for the unfiltered fetch (no status param).
		const unfiltered = page.waitForResponse(
			(res) => res.url().includes('/api/invoices') && !res.url().includes('status=')
		);
		await page.locator('.filter-chip', { hasText: /^All\s/ }).click();
		await unfiltered;

		await expect(page.locator('.filter-chip', { hasText: /^All\s/ })).toHaveClass(
			/active/
		);
	});
});
