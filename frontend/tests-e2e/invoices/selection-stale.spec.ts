import { expect, test } from '../fixtures/helpers';

/**
 * /invoices — selection must not go stale across a filter refetch.
 *
 * Regression for the stale-selection bug: selecting rows, then narrowing the
 * list with a status chip, used to leave the selection (and the bulk-bar count)
 * pointing at ids that fell off the list — so the bulk delete/status/export
 * acted on rows the user could no longer see. The page now prunes the selection
 * to the visible ids on every refetch (via `pruneSelection`), mirroring the
 * exceptions queue.
 *
 * Invariant asserted (the one the bug violated): the bulk-bar count must always
 * equal the number of *visible* checked checkboxes. With the stale selection
 * retained, the count outran the visible checked rows whenever a selected row
 * scrolled off under the filter.
 */

test.describe('/invoices selection pruning (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/invoices');
		await expect(page.locator('table tbody tr').first()).toBeVisible();
	});

	test('bulk count tracks visible checked rows after a filter change', async ({ page }) => {
		// Select every currently-selectable row on the default (unfiltered) view.
		const enabled = page.locator(
			'table tbody tr td.checkbox-col input[type="checkbox"]:not([disabled])'
		);
		const initialCount = await enabled.count();
		test.skip(initialCount === 0, 'seed has no selectable invoices on the first page');
		for (let i = 0; i < initialCount; i++) await enabled.nth(i).check();
		await expect(page.locator('.bulk-count')).toContainText(`${initialCount} selected`);

		// Narrow the list with the always-rendered quick-subset "New" status chip.
		// This refetches and (for any non-`new` selected row) drops it off the list.
		const newChip = page.locator('.filter-chip', { hasText: /^New\b/ }).first();
		await newChip.click();
		await expect(page.locator('table tbody tr').first()).toBeVisible();

		// Invariant: bulk count == number of visible checked checkboxes. The
		// pruning effect keeps these in lockstep; the stale-selection bug let the
		// count exceed the visible checked rows.
		const visibleChecked = await page
			.locator('table tbody tr td.checkbox-col input[type="checkbox"]:checked')
			.count();

		const bar = page.locator('.bulk-bar');
		if (visibleChecked === 0) {
			// All selected rows fell off → the bar must be gone (count 0).
			await expect(bar).toHaveCount(0);
			return;
		}
		await expect(page.locator('.bulk-count')).toContainText(`${visibleChecked} selected`);
	});
});
