import { expect, test, ACME_BASE } from '../fixtures/helpers';

import { signInAndWait } from '../fixtures/helpers';

// Pinned to the acme tenant: this spec uses ACME_*/TECHFLOW_* creds or
// asserts cross-tenant isolation that requires fixed tenant slugs. The
// per-worker baseURL from fixtures/helpers.ts would otherwise route to
// the wrong tenant. Multiple workers may share acme here — keep this
// file's tests read-only or idempotent.
test.use({ baseURL: ACME_BASE });

/**
 * Token tampering — any client-side modification of the stored JWT must
 * cause the next protected request to fail closed and bounce the user
 * to /login. Three vectors are exercised:
 *
 *   1. Signature byte-flip — the structure parses but verify() fails
 *   2. Garbage / non-JWT string — parser errors out
 *   3. Empty string — distinct from "no token" because auth.svelte.ts
 *      treats "" differently from `null` in some paths
 *
 * The positive control (reload preserves a valid session) is here to
 * catch a regression where the redirect logic fires for everyone
 * because the SPA forgot to re-hydrate auth state on boot.
 */

const API_URL = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

async function getStoredToken(page: import('@playwright/test').Page): Promise<string> {
	const token = await page.evaluate(() => localStorage.getItem('auth_token'));
	if (!token) throw new Error('expected auth_token to be set');
	return token;
}

test.describe('token tampering (acme admin)', () => {
	test('byte-flipped signature → next protected request gets 401', async ({ page, request }) => {
		await signInAndWait(page);
		const valid = await getStoredToken(page);

		// JWT = header.payload.signature. Flip a character in the
		// signature segment so verification fails — keeping the
		// structure intact ensures we exercise the verify path, not the
		// parse path.
		const parts = valid.split('.');
		expect(parts).toHaveLength(3);
		const tamperedSig = parts[2].slice(0, -1) + (parts[2].slice(-1) === 'A' ? 'B' : 'A');
		const tampered = [parts[0], parts[1], tamperedSig].join('.');

		const res = await request.get(`${API_URL}/api/invoices`, {
			headers: {
				Authorization: `Bearer ${tampered}`,
				'X-Tenant-Slug': 'acme'
			}
		});
		expect(res.status()).toBe(401);
	});

	test('byte-flipped signature in localStorage → SPA bounces to /login', async ({ page }) => {
		await signInAndWait(page);
		const valid = await getStoredToken(page);
		const parts = valid.split('.');
		const tampered = [
			parts[0],
			parts[1],
			parts[2].slice(0, -1) + (parts[2].slice(-1) === 'A' ? 'B' : 'A')
		].join('.');
		await page.evaluate((t) => localStorage.setItem('auth_token', t), tampered);

		// Force a fresh API request — invoices page boot calls
		// /api/invoices. api.ts catches the 401 and reroutes.
		await page.goto('/invoices');
		await page.waitForURL(/\/login/, { timeout: 10_000 });

		// auth.ts.clearToken() runs on 401, so the tampered token must
		// be gone from storage too — otherwise the next reload loops.
		const after = await page.evaluate(() => localStorage.getItem('auth_token'));
		expect(after).toBeNull();
	});

	test('non-JWT garbage in localStorage → /login', async ({ page }) => {
		await signInAndWait(page);
		await page.evaluate(() => localStorage.setItem('auth_token', 'not-a-jwt-at-all'));

		await page.goto('/invoices');
		await page.waitForURL(/\/login/, { timeout: 10_000 });
	});

	test('empty token string in localStorage → /login', async ({ page }) => {
		await signInAndWait(page);
		await page.evaluate(() => localStorage.setItem('auth_token', ''));

		await page.goto('/invoices');
		await page.waitForURL(/\/login/, { timeout: 10_000 });
	});

	test('valid token survives reload (positive control)', async ({ page }) => {
		await signInAndWait(page);

		// Sanity check that a reload doesn't accidentally trash the
		// session. Without this, the negative tests above could pass
		// because the SPA always boots anon — making them meaningless.
		await page.reload();
		await page.waitForLoadState('networkidle');

		const token = await page.evaluate(() => localStorage.getItem('auth_token'));
		expect(token, 'reload must preserve the session').toBeTruthy();
		// And we must not be sitting on /login.
		expect(page.url()).not.toMatch(/\/login/);
	});

	test('direct API call with no Authorization header → 401', async ({ request }) => {
		const res = await request.get(`${API_URL}/api/invoices`, {
			headers: { 'X-Tenant-Slug': 'acme' }
		});
		expect(res.status()).toBe(401);
	});

	test('direct API call with malformed Authorization header → 401', async ({ request }) => {
		// Common shape of attacker probing: "Bearer" with no token,
		// "Basic" instead of "Bearer", or just garbage.
		for (const auth of ['Bearer', 'Bearer ', 'Basic xyz', 'totally-not-a-header']) {
			const res = await request.get(`${API_URL}/api/invoices`, {
				headers: { Authorization: auth, 'X-Tenant-Slug': 'acme' }
			});
			expect(res.status(), `expected 401 for "${auth}"`).toBe(401);
		}
	});
});
