import { expect, signInAndWait, test } from '../fixtures/helpers';
import type { Route } from '@playwright/test';

/**
 * /admin/api-keys — the usage readout must answer about the key it names.
 *
 * `GET /api/api-keys/{id}/usage` is a re-issuable fetch keyed on which row was
 * clicked, so it takes a request-identity guard (`frontend/CLAUDE.md`
 * § Sequencing list fetches): open key A's usage, close it, open B's, and
 * without one A's late response resolves into `usage` afterwards — so B's name
 * sits in the modal heading above A's request counts. A per-key meter read under
 * the wrong key's name is how a quota decision (revoke this key, raise that
 * plan) gets made on someone else's traffic.
 *
 * The page got that sequencer alongside the other three modal surfaces
 * (`decisions §106`); this spec is the coverage that shipment did not include,
 * and the direct sibling of `tests-e2e/experiments/results-identity.spec.ts`.
 *
 * Both responses are stubbed and their ORDER is controlled: A's is parked on a
 * promise this spec resolves by hand. No sleeps, no inflated timeouts.
 *
 * Identity is read off the numbers rather than a `data-…` attribute: unlike the
 * experiments modal, this one exposes no id, and the totals ARE the answer under
 * test. A and B are given deliberately distinct three-digit counts (no thousands
 * separator in any locale), so a stale commit is unambiguous rather than
 * plausible.
 */

const KEY_A = 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa';
const KEY_B = 'bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb';

const A_TOTAL = 413;
const B_TOTAL = 871;

function keyRow(id: string, name: string, prefix: string) {
	return {
		id,
		name,
		key_prefix: prefix,
		scopes: ['read'],
		created_at: '2024-01-01T00:00:00Z',
		last_used_at: '2024-01-02T00:00:00Z',
		revoked_at: null
	};
}

function usageFor(id: string, prefix: string, total: number) {
	return {
		api_key_id: id,
		key_prefix: prefix,
		total_requests: total,
		window_days: 30,
		window_requests: total,
		last_used_at: '2024-01-02T00:00:00Z',
		daily: [{ usage_date: '2024-01-02', request_count: total }]
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
	return request.method() === 'GET' && new URL(request.url()).pathname === '/api/api-keys';
}

/** The API-key id a usage request is for, else null. */
function usageKeyId(url: string): string | null {
	const m = new URL(url).pathname.match(/^\/api\/api-keys\/([^/]+)\/usage$/);
	return m ? m[1] : null;
}

test.describe('/admin/api-keys usage identity', () => {
	// Explicit sign-in, matching `admin/api-keys.spec.ts`: every endpoint behind
	// this page is admin-only, and leaning on the shared storage cache is what
	// that file documents as unreliable here.
	test.use({ storageState: { cookies: [], origins: [] } });

	test("a late usage response cannot land under a second key's name", async ({ page }) => {
		await signInAndWait(page);

		let releaseA!: () => void;
		const heldA = new Promise<void>((resolve) => {
			releaseA = resolve;
		});
		let aRequested = false;
		let listRequests = 0;

		// Every branch is decided on the real PATHNAME and anything else is passed
		// straight through — the same discipline the experiments sibling documents
		// for Vite's dev-server module URLs.
		await page.route('**/api/api-keys**', async (route: Route) => {
			if (!new URL(route.request().url()).pathname.startsWith('/api/api-keys')) {
				await route.continue();
				return;
			}
			if (isListGet(route.request())) {
				listRequests += 1;
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify([
						keyRow(KEY_A, 'Race Alpha', 'feoh_live_aaaa'),
						keyRow(KEY_B, 'Race Bravo', 'feoh_live_bbbb')
					])
				});
				return;
			}
			const id = usageKeyId(route.request().url());
			if (id === KEY_A) {
				aRequested = true;
				await heldA;
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify(usageFor(KEY_A, 'feoh_live_aaaa', A_TOTAL))
				});
				return;
			}
			if (id === KEY_B) {
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify(usageFor(KEY_B, 'feoh_live_bbbb', B_TOTAL))
				});
				return;
			}
			await route.continue();
		});

		try {
			await page.goto('/admin/api-keys');

			const rowA = page.locator('table tbody tr', { hasText: 'Race Alpha' });
			const rowB = page.locator('table tbody tr', { hasText: 'Race Bravo' });
			await expect(rowA).toBeVisible({ timeout: 15_000 });

			const usageModal = page.getByRole('dialog', { name: 'API key usage' });
			const totals = page.getByTestId('usage-totals');

			// 1. Open A's usage — its request is issued and then held, so the modal
			//    is stuck on its loading state with no totals rendered.
			await rowA.locator('.row-link').click();
			await expect.poll(() => aRequested).toBe(true);
			await expect(page.getByTestId('usage-loading')).toBeVisible();
			await expect(totals).toHaveCount(0);

			// 2. Close A and open B. B's response resolves normally.
			await page.keyboard.press('Escape');
			await expect(usageModal).toHaveCount(0);
			await rowB.locator('.row-link').click();
			await expect(totals).toContainText(String(B_TOTAL), { timeout: 15_000 });
			await expect(usageModal).toContainText('Race Bravo');

			// 3. Release A's response LAST and wait for its body to be fully
			//    received by the browser — a real completion signal, not a timer.
			const aLanded = page.waitForResponse((r) => usageKeyId(r.url()) === KEY_A, {
				timeout: 15_000
			});
			releaseA();
			await (await aLanded).finished();

			// An ordering barrier: one more real round trip issued from inside the
			// page and awaited to completion. Everything the page had already
			// queued — including the response that arrived above — has run by the
			// time it resolves. There is no fixed delay anywhere in this spec.
			await pageRoundTrip(page);

			// The heading is the modal's SUBJECT (key B) and the totals are the
			// RESPONSE. Both must still be B's: pre-fix the stale A response
			// overwrote `usage` and the two disagreed, permanently, because nothing
			// else fetches.
			await expect(usageModal).toContainText('Race Bravo');
			await expect(totals).toContainText(String(B_TOTAL));
			await expect(totals).not.toContainText(String(A_TOTAL));
			expect(listRequests).toBeGreaterThan(0);
		} finally {
			releaseA();
			await page.unroute('**/api/api-keys**').catch(() => {});
		}
	});
});
