import { expect, test } from '../fixtures/helpers';

/**
 * /payments — the Runs tab distinguishes loading / failed / genuinely empty.
 *
 * The Queue and History tabs in this same file already did. The Runs tab had no
 * loading flag at all and reported its failure only through a toast, so
 * `isEmpty={runs.length === 0}` asserted "No payment runs yet." while the first
 * fetch was still in flight — and permanently after a failed one, with the
 * toast that explained it already faded. On this tab that message is a claim
 * about whether this tenant has ever moved money.
 *
 * Stubbed so both states are deterministic: a real backend answers instantly
 * and never fails on demand.
 */

const RUNS_PATH = '/api/payments/runs/';

test.describe('/payments Runs tab load states', () => {
	test('a FAILED load offers a retry — never "No payment runs yet."', async ({ page }) => {
		let attempts = 0;
		await page.route('**/api/payments/runs/*', async (route) => {
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

		await page.goto('/payments?tab=runs');

		const error = page.getByTestId('runs-error');
		await expect(error).toBeVisible({ timeout: 10_000 });
		await expect(error).toContainText('Could not load payment runs.');
		// The critical negative: an outage must not read as "you have no runs".
		await expect(page.getByTestId('table-empty')).toHaveCount(0);

		// …and the failure is not a dead end.
		await error.getByRole('button', { name: 'Retry' }).click();
		const empty = page.getByTestId('table-empty');
		await expect(empty).toBeVisible({ timeout: 10_000 });
		await expect(empty).toHaveText('No payment runs yet.');
		expect(attempts).toBeGreaterThan(1);
	});

	test('a SLOW load shows the loading state, not the empty claim', async ({ page }) => {
		// A real readiness gate, not a sleep: the response is released by
		// resolving this promise, so nothing is timing-dependent.
		let release!: () => void;
		const held = new Promise<void>((resolve) => {
			release = resolve;
		});
		await page.route('**/api/payments/runs/*', async (route) => {
			if (!new URL(route.request().url()).pathname.startsWith(RUNS_PATH)) {
				await route.continue();
				return;
			}
			await held;
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 100 })
			});
		});

		await page.goto('/payments?tab=runs');

		const empty = page.getByTestId('table-empty');
		await expect(empty).toBeVisible({ timeout: 10_000 });
		await expect(empty).toHaveText('Loading…');
		await expect(empty).not.toContainText('No payment runs yet.');

		release();

		// Once the genuinely-empty response lands, the empty claim is earned.
		await expect(empty).toHaveText('No payment runs yet.', { timeout: 10_000 });
	});
});
