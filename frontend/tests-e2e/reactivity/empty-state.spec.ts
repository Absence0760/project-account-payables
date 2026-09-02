import { expect, test } from '../fixtures/helpers';

/**
 * The onboarding <EmptyState> (issue #328 — persona-new-user): a brand-new
 * zero-data tenant gets a heading + description + a primary action, NOT a bare
 * "no results" line. It renders ONLY for the genuinely-empty-and-unfiltered
 * case — a filter that matched nothing keeps the plain "no match" copy
 * (frontend/CLAUDE.md § Data tables).
 *
 * The seed always has invoices, so the empty responses here are route-mocked
 * (same technique as search-debounce-race.spec.ts). This is the guard on the
 * distinction that matters; the dashboard + portal adoptions are the same
 * component and are covered by `pnpm check` + the a11y token-pairing guard.
 */

test('/invoices: onboarding CTA when truly empty, plain copy when a filter matched nothing', async ({
	page
}) => {
	await page.route('**/api/invoices**', async (route) => {
		const url = new URL(route.request().url());
		if (url.pathname === '/api/invoices') {
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 20 })
			});
		}
		if (url.pathname === '/api/invoices/counts') {
			return route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ counts: {}, total: 0 })
			});
		}
		return route.continue();
	});

	await page.goto('/invoices');

	// Unfiltered + empty → the rich onboarding block with its action.
	const onboarding = page.getByTestId('invoices-empty-state');
	await expect(onboarding).toBeVisible();
	await expect(onboarding.getByRole('button', { name: /upload invoices/i })).toBeVisible();
	// The plain table-empty cell is NOT what's showing.
	await expect(page.getByTestId('table-empty')).toHaveCount(0);

	// Apply a status filter → still zero rows, but now it's "nothing matched",
	// so the plain copy replaces the onboarding block.
	await page.locator('.filter-chip', { hasText: /^New\s/ }).first().click();
	await expect(page.getByTestId('invoices-empty-state')).toHaveCount(0);
	await expect(page.getByTestId('table-empty')).toHaveText(/no invoices match your filters/i);
});
