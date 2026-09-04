import { expect, test } from '../fixtures/helpers';

/**
 * /experiments — a failed load explains itself once, and offers a way back.
 *
 * The page rendered its error banner AND "No experiments yet." at the same
 * time — two contradictory statements — because `isEmpty` was gated on
 * `!loading` rather than composing the three states, and the banner came from a
 * dependency-free single-shot `$effect` with no control that could re-run it.
 *
 * Stubbed so both states are deterministic.
 */

/**
 * The list GET, and nothing else.
 *
 * The glob has to be checked against the PATHNAME: `**\/api/experiments*` also
 * matches Vite's dev-server module URL for `/src/lib/api/experiments.ts`, and
 * fulfilling that with JSON blanks the whole page (the route chunk never
 * loads). Same exact-pathname guard `tests-e2e/reactivity/list-load-failure`
 * uses for `/api/payments` vs `/api/payments/queue`.
 */
function isListGet(request: import('@playwright/test').Request): boolean {
	return (
		request.method() === 'GET' && new URL(request.url()).pathname === '/api/experiments'
	);
}

test.describe('/experiments load states', () => {
	test('a FAILED load offers a retry and never doubles up with "No experiments yet."', async ({
		page
	}) => {
		let attempts = 0;
		await page.route('**/api/experiments*', async (route) => {
			if (!isListGet(route.request())) {
				await route.continue();
				return;
			}
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
				body: JSON.stringify({ experiments: [] })
			});
		});

		await page.goto('/experiments');

		const error = page.getByTestId('experiments-error');
		await expect(error).toBeVisible({ timeout: 10_000 });
		await expect(page.getByTestId('table-empty')).toHaveCount(0);

		await error.getByRole('button', { name: 'Retry' }).click();
		await expect(page.getByTestId('table-empty')).toHaveText('No experiments yet.', {
			timeout: 10_000
		});
		expect(attempts).toBeGreaterThan(1);
	});

	test('a SLOW load shows the loading state, not the empty claim', async ({ page }) => {
		// A real readiness gate, not a sleep.
		let release!: () => void;
		const held = new Promise<void>((resolve) => {
			release = resolve;
		});
		await page.route('**/api/experiments*', async (route) => {
			if (!isListGet(route.request())) {
				await route.continue();
				return;
			}
			await held;
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ experiments: [] })
			});
		});

		await page.goto('/experiments');

		const empty = page.getByTestId('table-empty');
		await expect(empty).toBeVisible({ timeout: 10_000 });
		await expect(empty).toHaveText('Loading…');

		release();

		await expect(empty).toHaveText('No experiments yet.', { timeout: 10_000 });
	});
});
