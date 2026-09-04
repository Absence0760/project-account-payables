import { expect, test } from '../fixtures/helpers';

/**
 * /catalogs — loading / failed / genuinely empty are three different states.
 *
 * `isEmpty` was gated on `!loading`, which inverts what the flag is for: during
 * the first fetch the table rendered its header with NOTHING under it — no
 * rows, no spinner, no message — and a failed load, reported only by a toast
 * that then faded, left "No catalogs." standing forever with no way to retry.
 *
 * Same three-state rule `/exceptions` and the payments queue follow, and the
 * same error-with-retry block `/admin/api-keys` uses.
 */

test.describe('/catalogs load states', () => {
	test('a SLOW load shows the loading state, not a bare header', async ({ page }) => {
		// A real readiness gate, not a sleep: the response is released by
		// resolving this promise, so nothing is timing-dependent.
		let release!: () => void;
		const held = new Promise<void>((resolve) => {
			release = resolve;
		});
		await page.route('**/api/catalogs?**', async (route) => {
			await held;
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 100 })
			});
		});

		await page.goto('/catalogs');

		const empty = page.getByTestId('table-empty');
		await expect(empty).toBeVisible({ timeout: 10_000 });
		await expect(empty).toHaveText('Loading…');

		release();

		await expect(empty).toHaveText('No catalogs.', { timeout: 10_000 });
	});

	test('a FAILED load offers a retry — never "No catalogs."', async ({ page }) => {
		let attempts = 0;
		await page.route('**/api/catalogs?**', async (route) => {
			attempts += 1;
			if (attempts === 1) {
				await route.fulfill({
					status: 500,
					contentType: 'application/json',
					body: JSON.stringify({ detail: 'boom' })
				});
				return;
			}
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 100 })
			});
		});

		await page.goto('/catalogs');

		const error = page.getByTestId('catalogs-error');
		await expect(error).toBeVisible({ timeout: 10_000 });
		await expect(error).toContainText('Could not load catalogs.');
		// The two must never be on screen together.
		await expect(page.getByTestId('table-empty')).toHaveCount(0);

		await error.getByRole('button', { name: 'Retry' }).click();
		await expect(page.getByTestId('table-empty')).toHaveText('No catalogs.', { timeout: 10_000 });
		expect(attempts).toBeGreaterThan(1);
	});
});
