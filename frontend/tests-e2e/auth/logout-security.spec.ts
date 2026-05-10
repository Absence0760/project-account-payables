import { expect, test } from '@playwright/test';

import { ACME_ADMIN, signInAndWait, signOut } from '../fixtures/helpers';

/**
 * Logout security — the backend keeps a Redis blocklist keyed on the
 * token's `jti`. `POST /api/auth/logout` adds the JTI for the
 * remainder of the token's TTL, so a copied / leaked token cannot be
 * reused after the user clicks Log Out. Without this contract, the
 * Log Out button would be cosmetic.
 *
 * Tests:
 *   1. Save the JWT, log out, then replay it via a direct API call —
 *      expect 401 (blocklist hit).
 *   2. Two-tab simulation: two browser contexts share a login (via
 *      copied token), one logs out, the other's next API call gets
 *      401. This proves the blocklist is global, not just local SPA
 *      state.
 */

const API_URL = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

test.describe('logout security', () => {
	test('logged-out JWT is rejected on subsequent direct API calls', async ({ page, request }) => {
		await signInAndWait(page, ACME_ADMIN);
		const tokenBeforeLogout = await page.evaluate(() => localStorage.getItem('auth_token'));
		expect(tokenBeforeLogout).toBeTruthy();

		// Sanity: token works before logout.
		const before = await request.get(`${API_URL}/api/invoices`, {
			headers: {
				Authorization: `Bearer ${tokenBeforeLogout}`,
				'X-Tenant-Slug': 'acme'
			}
		});
		expect(before.status(), 'token must work before logout').toBe(200);

		await signOut(page);

		// Replay the same token directly. If the blocklist works, we
		// get 401. If it doesn't, we'd get 200 and the user's session
		// is effectively unrevokable.
		const after = await request.get(`${API_URL}/api/invoices`, {
			headers: {
				Authorization: `Bearer ${tokenBeforeLogout}`,
				'X-Tenant-Slug': 'acme'
			}
		});
		expect(after.status(), 'token must be revoked after logout').toBe(401);
	});

	test('logout in one browser context invalidates the same JWT in another', async ({
		browser
	}) => {
		// Two contexts share a JWT by manual injection (the natural
		// shape of a stolen-and-replayed token). Logging out in one
		// must lock out the other on its next request.
		const ctxA = await browser.newContext();
		const ctxB = await browser.newContext();
		try {
			const pageA = await ctxA.newPage();
			await signInAndWait(pageA, ACME_ADMIN);
			const token = await pageA.evaluate(() => localStorage.getItem('auth_token'));
			expect(token).toBeTruthy();

			// Inject the token into B and confirm it can read data.
			const pageB = await ctxB.newPage();
			await pageB.goto('/login');
			await pageB.evaluate((t) => localStorage.setItem('auth_token', t!), token);
			const respBefore = await ctxB.request.get(`${API_URL}/api/invoices`, {
				headers: {
					Authorization: `Bearer ${token}`,
					'X-Tenant-Slug': 'acme'
				}
			});
			expect(respBefore.status()).toBe(200);

			// Log out from context A.
			await signOut(pageA);

			// Context B's next API call must now be rejected — the
			// blocklist is global, not per-context.
			const respAfter = await ctxB.request.get(`${API_URL}/api/invoices`, {
				headers: {
					Authorization: `Bearer ${token}`,
					'X-Tenant-Slug': 'acme'
				}
			});
			expect(respAfter.status()).toBe(401);
		} finally {
			await ctxA.close();
			await ctxB.close();
		}
	});

	test('logout endpoint requires authentication', async ({ request }) => {
		// Anon logout would be a denial-of-service vector if it
		// accepted any JTI from a query param — confirm the endpoint
		// itself is auth-gated. (We don't care about the exact 401 vs
		// 422 shape, only that it's not a 2xx.)
		const res = await request.post(`${API_URL}/api/auth/logout`, {
			headers: { 'X-Tenant-Slug': 'acme' }
		});
		expect(res.status()).toBeGreaterThanOrEqual(400);
		expect(res.status()).toBeLessThan(500);
	});

	test('localStorage is cleared on logout', async ({ page }) => {
		// Redundant with signout.spec.ts but documents this as a
		// security property too: even if the blocklist were lost on a
		// Redis flush, the local copy of the token is gone.
		await signInAndWait(page, ACME_ADMIN);
		await signOut(page);

		const token = await page.evaluate(() => localStorage.getItem('auth_token'));
		expect(token).toBeNull();
	});
});
