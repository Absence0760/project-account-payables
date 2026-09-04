import { expect, test } from '../fixtures/helpers';

/**
 * /reports — a failed load is recoverable, and a loading table says so.
 *
 * Two defects, one shape. `GET /api/reports/catalog` drives the WHOLE builder,
 * so its failure has to replace it — but it was replaced by a bare paragraph,
 * fired from a single-shot dependency-free `$effect`, leaving nothing on screen
 * that could re-run the load: the page was a dead end until a manual reload.
 * And the saved-reports table gated `isEmpty` on `!savedLoading`, so during the
 * fetch it rendered a header with nothing under it, and on failure it rendered
 * an error banner ON TOP of "No saved reports yet." — two contradictory
 * statements at once.
 *
 * Stubbed so both states are deterministic.
 */

const CATALOG = {
	sources: [
		{
			key: 'invoices',
			label: 'Invoices',
			dimensions: [{ key: 'status', label: 'Status', type: 'enum', values: ['approved'] }],
			measures: [{ key: 'amount', label: 'Amount', type: 'money' }],
			filters: []
		}
	]
};

test.describe('/reports load states', () => {
	test('a FAILED catalog load can be retried in place', async ({ page }) => {
		let attempts = 0;
		await page.route('**/api/reports/catalog*', async (route) => {
			attempts += 1;
			if (attempts === 1) {
				await route.fulfill({
					status: 500,
					contentType: 'application/json',
					body: JSON.stringify({ detail: 'catalog is down' })
				});
				return;
			}
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(CATALOG)
			});
		});
		await page.route('**/api/reports?**', (route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ reports: [] })
			})
		);

		await page.goto('/reports');

		const error = page.getByTestId('catalog-error');
		await expect(error).toBeVisible({ timeout: 10_000 });

		// The whole point: a way back, without a manual page reload.
		await error.getByRole('button', { name: 'Retry' }).click();
		await expect(page.getByLabel('Data source')).toBeVisible({ timeout: 10_000 });
		expect(attempts).toBeGreaterThan(1);
	});

	test('a FAILED saved-reports load never renders alongside "No saved reports yet."', async ({
		page
	}) => {
		await page.route('**/api/reports/catalog*', (route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(CATALOG)
			})
		);
		let attempts = 0;
		await page.route('**/api/reports', async (route) => {
			if (route.request().method() !== 'GET') {
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
				body: JSON.stringify({ reports: [] })
			});
		});

		await page.goto('/reports');

		const error = page.getByTestId('saved-error');
		await expect(error).toBeVisible({ timeout: 10_000 });
		await expect(page.getByTestId('table-empty')).toHaveCount(0);

		await error.getByRole('button', { name: 'Retry' }).click();
		await expect(page.getByTestId('table-empty')).toHaveText('No saved reports yet.', {
			timeout: 10_000
		});
		expect(attempts).toBeGreaterThan(1);
	});

	test('a SLOW saved-reports load shows the loading state, not a bare header', async ({ page }) => {
		await page.route('**/api/reports/catalog*', (route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(CATALOG)
			})
		);
		// A real readiness gate, not a sleep.
		let release!: () => void;
		const held = new Promise<void>((resolve) => {
			release = resolve;
		});
		await page.route('**/api/reports', async (route) => {
			if (route.request().method() !== 'GET') {
				await route.continue();
				return;
			}
			await held;
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ reports: [] })
			});
		});

		await page.goto('/reports');

		const empty = page.getByTestId('table-empty');
		await expect(empty).toBeVisible({ timeout: 10_000 });
		await expect(empty).toHaveText('Loading…');

		release();

		await expect(empty).toHaveText('No saved reports yet.', { timeout: 10_000 });
	});
});
