import { expect, test } from '../fixtures/helpers';
import type { Route } from '@playwright/test';

/**
 * /experiments — the results readout must answer about the experiment it names.
 *
 * `GET /api/experiments/{id}/results` had no request-identity guard: open
 * experiment A's readout, close it, open B's, and A's response resolved into
 * `results` afterwards — so B's modal title sat above A's variant metrics and
 * winner call. A winner attributed to the wrong experiment is how a workflow
 * rule gets rolled out on someone else's numbers.
 *
 * Both responses are stubbed and their ORDER is controlled: A's is parked on a
 * promise this spec resolves by hand. No sleeps, no inflated timeouts.
 *
 * The exact-pathname guard on the list GET is the same one
 * `experiments/load-states.spec.ts` documents — `**\/api/experiments*` also
 * matches Vite's dev-server module URL for `src/lib/api/experiments.ts`.
 */

const EXP_A = 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa';
const EXP_B = 'bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb';

function experimentRow(id: string, name: string) {
	return {
		id,
		name,
		description: '',
		status: 'running',
		workflow_definition_name: 'Default workflow',
		assigned_count: 40,
		workflow_definition_id: 'cccccccc-3333-4333-8333-cccccccccccc',
		split_a_pct: 50,
		primary_metric: 'time_to_approval_days',
		min_sample_per_variant: 10,
		config_a: { steps: [] },
		config_b: { steps: [] },
		started_at: '2024-01-01T00:00:00Z',
		ended_at: null,
		created_at: '2024-01-01T00:00:00Z',
		updated_at: '2024-01-01T00:00:00Z'
	};
}

function resultsFor(id: string, winner: 'a' | 'b') {
	return {
		experiment_id: id,
		primary_metric: 'time_to_approval_days',
		enough_data: true,
		winner,
		variant_a: {
			variant: 'a',
			sample_size: 40,
			time_to_approval_days: 3,
			touchless_rate_pct: 10,
			exception_rate_pct: 5,
			rejection_rate_pct: 2
		},
		variant_b: {
			variant: 'b',
			sample_size: 40,
			time_to_approval_days: 2,
			touchless_rate_pct: 20,
			exception_rate_pct: 4,
			rejection_rate_pct: 1
		},
		notes: []
	};
}

/**
 * An ordering barrier: a real same-origin round trip issued from the page and
 * awaited to completion. Not a sleep — there is no fixed delay; it resolves on
 * a genuine network completion, and anything the page queued beforehand has
 * run by then.
 */
async function pageRoundTrip(page: import('@playwright/test').Page): Promise<void> {
	await page.evaluate(async () => {
		await fetch(`/favicon.svg?barrier=${Date.now()}`, { cache: 'no-store' });
	});
}

function isListGet(request: import('@playwright/test').Request): boolean {
	return request.method() === 'GET' && new URL(request.url()).pathname === '/api/experiments';
}

/** The experiment id a results request is for, else null. */
function resultsExperimentId(url: string): string | null {
	const m = new URL(url).pathname.match(/^\/api\/experiments\/([^/]+)\/results$/);
	return m ? m[1] : null;
}

test.describe('/experiments results identity', () => {
	test("a late results response cannot land under a second experiment's name", async ({ page }) => {
		let releaseA!: () => void;
		const heldA = new Promise<void>((resolve) => {
			releaseA = resolve;
		});
		let aRequested = false;
		let listRequests = 0;

		// `**\/api/experiments**` also matches Vite's dev-server module URL for
		// `src/lib/api/experiments.ts`, so every branch is decided on the real
		// PATHNAME and anything else is passed straight through.
		await page.route('**/api/experiments**', async (route: Route) => {
			if (!new URL(route.request().url()).pathname.startsWith('/api/experiments')) {
				await route.continue();
				return;
			}
			if (isListGet(route.request())) {
				listRequests += 1;
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify({
						experiments: [
							experimentRow(EXP_A, 'Race Alpha'),
							experimentRow(EXP_B, 'Race Bravo')
						]
					})
				});
				return;
			}
			const id = resultsExperimentId(route.request().url());
			if (id === EXP_A) {
				aRequested = true;
				await heldA;
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify(resultsFor(EXP_A, 'a'))
				});
				return;
			}
			if (id === EXP_B) {
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify(resultsFor(EXP_B, 'b'))
				});
				return;
			}
			await route.continue();
		});

		try {
			await page.goto('/experiments');

			const rowA = page.locator('table tbody tr', { hasText: 'Race Alpha' });
			const rowB = page.locator('table tbody tr', { hasText: 'Race Bravo' });
			await expect(rowA).toBeVisible({ timeout: 15_000 });

			const readout = page.getByTestId('experiment-results');

			// 1. Open A's readout — its request is issued and then held.
			await rowA.locator('.row-link').click();
			await expect.poll(() => aRequested).toBe(true);
			await expect(readout).toHaveCount(0);

			// 2. Close A and open B. B's response resolves normally.
			await page.keyboard.press('Escape');
			await rowB.locator('.row-link').click();
			await expect(readout).toHaveAttribute('data-experiment-id', EXP_B, { timeout: 15_000 });
			await expect(readout).toHaveAttribute('data-results-for', EXP_B);

			// 3. Release A's response LAST and wait for its body to be fully
			//    received by the browser — a real completion signal, not a timer.
			const aLanded = page.waitForResponse((r) => resultsExperimentId(r.url()) === EXP_A, {
				timeout: 15_000
			});
			releaseA();
			await (await aLanded).finished();

			// An ordering barrier: one more real round trip issued from inside the
			// page and awaited to completion. Everything the page had already
			// queued — including the response that arrived above — has run by the
			// time it resolves. There is no fixed delay anywhere in this spec.
			await pageRoundTrip(page);

			// `data-results-for` is read off the RESPONSE, `data-experiment-id` off
			// the modal's subject. Both must still be B: pre-fix the stale A
			// response overwrote `results` and they disagreed, permanently, because
			// nothing else fetches.
			await expect(readout).toHaveAttribute('data-experiment-id', EXP_B);
			await expect(readout).toHaveAttribute('data-results-for', EXP_B);
			expect(listRequests).toBeGreaterThan(0);
		} finally {
			releaseA();
			await page.unroute('**/api/experiments**').catch(() => {});
		}
	});
});
