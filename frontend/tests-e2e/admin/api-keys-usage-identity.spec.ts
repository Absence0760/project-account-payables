import type { Route } from '@playwright/test';

import { expect, test } from '../fixtures/helpers';

/**
 * /admin/api-keys — the per-key usage panel must answer about the key it names.
 *
 * `GET /api/api-keys/{id}/usage` is a re-issuable fetch keyed on which row was
 * clicked: open key A's usage, close it, open key B's, and A's response can
 * resolve afterwards. `usageSequence` (a `createRequestSequencer`) is what stops
 * it committing — but that guard shipped without an e2e, because the
 * request-identity sweep that added it did not reach this spec directory. A
 * guard nothing drives is a guard nobody notices removing.
 *
 * It matters here for the same reason it does on `/audit` and `/experiments`:
 * the heading comes from the CLICK and the figures come from the RESPONSE, so a
 * mismatch renders as ordinary data. "This key has made 9 000 requests" under
 * the wrong key's name is how a live integration gets revoked by mistake.
 *
 * Both responses are stubbed and their ORDER is controlled — key A's is parked
 * on a promise this spec resolves by hand. No sleeps, no inflated timeouts: the
 * race is driven by a real readiness gate.
 */

const KEY_A = 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa';
const KEY_B = 'bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb';

const NAME_A = 'usage-identity-alpha';
const NAME_B = 'usage-identity-bravo';

const KEY_LIST = [
	{
		id: KEY_A,
		name: NAME_A,
		key_prefix: 'feoh_live_aaaa',
		scopes: ['read'],
		created_at: '2024-01-01T00:00:00Z',
		last_used_at: '2024-06-01T00:00:00Z',
		revoked_at: null
	},
	{
		id: KEY_B,
		name: NAME_B,
		key_prefix: 'feoh_live_bbbb',
		scopes: ['read'],
		created_at: '2024-02-02T00:00:00Z',
		last_used_at: '2024-06-02T00:00:00Z',
		revoked_at: null
	}
];

/** Distinct request totals, so which response is on screen is unambiguous. */
function usageFor(id: string, prefix: string, total: number, day: string) {
	return {
		api_key_id: id,
		key_prefix: prefix,
		total_requests: total,
		window_days: 30,
		window_requests: total,
		last_used_at: '2024-06-03T00:00:00Z',
		daily: [{ usage_date: day, request_count: total }]
	};
}

/** The key id a usage request is for, else null. */
function usageKeyId(url: string): string | null {
	const m = new URL(url).pathname.match(/^\/api\/api-keys\/([^/]+)\/usage$/);
	return m ? m[1] : null;
}

test.describe('/admin/api-keys usage identity', () => {
	test("a late usage response cannot land under a second key's name", async ({ page }) => {
		let releaseA!: () => void;
		const heldA = new Promise<void>((resolve) => {
			releaseA = resolve;
		});
		let aRequested = false;

		await page.route('**/api/api-keys**', async (route: Route) => {
			const url = new URL(route.request().url());
			if (url.pathname === '/api/api-keys' && route.request().method() === 'GET') {
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify(KEY_LIST)
				});
				return;
			}
			const keyId = usageKeyId(route.request().url());
			if (keyId === KEY_A) {
				aRequested = true;
				await heldA;
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify(usageFor(KEY_A, 'feoh_live_aaaa', 9111, '2024-06-01'))
				});
				return;
			}
			if (keyId === KEY_B) {
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify(usageFor(KEY_B, 'feoh_live_bbbb', 2222, '2024-06-02'))
				});
				return;
			}
			await route.continue();
		});

		try {
			await page.goto('/admin/api-keys');

			const rowA = page.getByRole('button', { name: `View usage for ${NAME_A}` });
			const rowB = page.getByRole('button', { name: `View usage for ${NAME_B}` });
			await expect(rowA).toBeVisible({ timeout: 15_000 });

			const dialog = page.getByRole('dialog', { name: 'API key usage' });
			const panel = page.getByTestId('api-key-usage');

			// 1. Open A's usage — its request is issued and then held.
			await rowA.click();
			await expect(panel).toHaveAttribute('data-key-id', KEY_A);
			await expect(page.getByTestId('usage-loading')).toBeVisible();
			await expect.poll(() => aRequested).toBe(true);

			// 2. Close it and open B's. B's response resolves normally.
			await dialog.getByRole('button', { name: 'Close' }).click();
			await expect(dialog).toBeHidden();
			await rowB.click();
			await expect(panel).toHaveAttribute('data-key-id', KEY_B);
			await expect(panel).toHaveAttribute('data-usage-for', KEY_B, { timeout: 15_000 });
			await expect(panel).toContainText('2,222');

			// 3. Release A's response LAST and wait for it to actually arrive.
			const aLanded = page.waitForResponse((r) => usageKeyId(r.url()) === KEY_A, {
				timeout: 15_000
			});
			releaseA();
			await aLanded;

			// An ordering barrier that touches the page but NOT the usage state: a
			// page-initiated round trip to the (stubbed) list endpoint, whose result
			// nothing consumes. A full network round trip is many event-loop turns,
			// so by the time it resolves anything A's `.then` was going to write has
			// already been written — `waitForResponse` alone only proves the bytes
			// arrived, not that the continuation ran.
			await page.evaluate(async () => {
				await fetch('/api/api-keys').catch(() => {});
			});

			// The panel is still B's, in both the name it shows and the figures.
			await expect(panel).toHaveAttribute('data-key-id', KEY_B);
			await expect(panel).toHaveAttribute('data-usage-for', KEY_B);
			await expect(panel).toContainText('2,222');
			await expect(panel).not.toContainText('9,111');
		} finally {
			releaseA();
			await page.unroute('**/api/api-keys**').catch(() => {});
		}
	});
});
