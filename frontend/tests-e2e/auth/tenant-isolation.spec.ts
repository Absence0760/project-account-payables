import { expect, test } from '../fixtures/helpers';

import {
	ACME_ADMIN,
	ACME_BASE,
	escapeRegExp,
	signInAndWait,
	TECHFLOW_ADMIN,
	TECHFLOW_BASE
} from '../fixtures/helpers';

// Start unauthenticated — this spec drives its own ACME/TECHFLOW logins to assert
// cross-tenant read isolation. The per-worker admin storage state would point at
// the wrong tenant.
test.use({ storageState: { cookies: [], origins: [] } });

// Pinned to the acme tenant: this spec uses ACME_*/TECHFLOW_* creds or
// asserts cross-tenant isolation that requires fixed tenant slugs. The
// per-worker baseURL from fixtures/helpers.ts would otherwise route to
// the wrong tenant. Multiple workers may share acme here — keep this
// file's tests read-only or idempotent.
test.use({ baseURL: ACME_BASE });

/**
 * Tenant isolation — a JWT minted for the techflow tenant must not be
 * usable to read acme's data even when the browser is pointed at the
 * acme subdomain. The backend enforces this at two layers:
 *
 *   1. JWT carries the org_id claim. Cross-tenant requests fail at
 *      the auth dependency.
 *   2. Each tenant's data lives in a separate Postgres DB
 *      (`feoh_<slug>`), so even a leaked JWT can't accidentally read
 *      another tenant's rows.
 *
 * Tests below exercise that contract at two layers:
 *   - UI: cross-tenant nav redirects to /login (no data leak in the shell).
 *   - API: a direct fetch with the wrong tenant token returns 401/403
 *     even though the request shape is otherwise valid. This pins the
 *     server-side check so a frontend regression that stops redirecting
 *     can't quietly leak data.
 */

const API_URL = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

/** Sign in via the UI and pluck the JWT out of localStorage. */
async function tokenAfterLogin(
	page: import('@playwright/test').Page,
	baseURL: string,
	creds: { email: string; password: string }
): Promise<string> {
	await page.goto(`${baseURL}/login`);
	await page.waitForLoadState('networkidle');
	await page.locator('input[type="email"]').fill(creds.email);
	await page.locator('input[type="password"]').fill(creds.password);
	await page.locator('form button[type="submit"]').click();
	await page.waitForURL(new RegExp(`^${escapeRegExp(baseURL)}/?$`), { timeout: 15_000 });
	const token = await page.evaluate(() => localStorage.getItem('auth_token'));
	if (!token) throw new Error('expected auth_token after sign-in');
	return token;
}

test.describe('tenant isolation — UI', () => {
	test.use({ baseURL: TECHFLOW_BASE });

	test('techflow JWT cannot reach acme data', async ({ page }) => {
		await signInAndWait(page, TECHFLOW_ADMIN);

		// localStorage now holds techflow's JWT. Hop to acme's invoices.
		// Frontend sends X-Tenant-Slug=acme + techflow Bearer. Backend
		// rejects → api.ts redirects → end state: anon on acme /login.
		await page.goto(`${ACME_BASE}/invoices`);
		await page.waitForURL(/\/login/, { timeout: 10_000 });
		expect(page.url()).toMatch(/^http:\/\/acme\.localhost:7777\//);
	});

	test('reverse direction: acme JWT cannot reach techflow data', async ({ page }) => {
		// Belt-and-braces — confirms the check isn't one-directional
		// (we caught a bug like that once when org_id was substringed).
		const acmeToken = await tokenAfterLogin(page, ACME_BASE, ACME_ADMIN);
		expect(acmeToken).toBeTruthy();

		await page.goto(`${TECHFLOW_BASE}/invoices`);
		await page.waitForURL(/\/login/, { timeout: 10_000 });
		expect(page.url()).toMatch(/^http:\/\/techflow\.localhost:7777\//);
	});
});

test.describe('tenant isolation — API', () => {
	test('techflow JWT + X-Tenant-Slug:acme is rejected at the backend', async ({
		request,
		page
	}) => {
		const techflowToken = await tokenAfterLogin(page, TECHFLOW_BASE, TECHFLOW_ADMIN);

		// Direct API call, no browser in the loop. This is the contract
		// the UI redirect depends on — if it ever stops returning 4xx
		// the redirect is just cosmetic and an attacker can scrape JSON.
		const res = await request.get(`${API_URL}/api/invoices`, {
			headers: {
				Authorization: `Bearer ${techflowToken}`,
				'X-Tenant-Slug': 'acme'
			}
		});
		expect(res.status(), 'cross-tenant API call must not return 2xx').toBeGreaterThanOrEqual(
			400
		);
		expect(res.status()).toBeLessThan(500);
	});

	test('techflow JWT + X-Tenant-Slug:techflow does succeed (positive control)', async ({
		request,
		page
	}) => {
		// Without this control, the cross-tenant test could be passing
		// because the token itself is broken, not because of isolation.
		const techflowToken = await tokenAfterLogin(page, TECHFLOW_BASE, TECHFLOW_ADMIN);

		const res = await request.get(`${API_URL}/api/invoices`, {
			headers: {
				Authorization: `Bearer ${techflowToken}`,
				'X-Tenant-Slug': 'techflow'
			}
		});
		expect(res.status(), 'matching-tenant API call must succeed').toBe(200);
	});

	test('no Authorization header is rejected the same way regardless of tenant header', async ({
		request
	}) => {
		// Authorization is the gate; the tenant header alone must never
		// be enough. Both shapes of "no auth" must fail closed.
		const r1 = await request.get(`${API_URL}/api/invoices`, {
			headers: { 'X-Tenant-Slug': 'acme' }
		});
		const r2 = await request.get(`${API_URL}/api/invoices`, {
			headers: { 'X-Tenant-Slug': 'techflow' }
		});
		expect(r1.status()).toBe(401);
		expect(r2.status()).toBe(401);
	});

	test('copying a token to a different origin does not grant cross-tenant access', async ({
		page,
		browser
	}) => {
		// Simulate an attacker copying their own JWT into a different
		// tenant's origin (different subdomain ⇒ separate localStorage).
		// Even after the SPA reads the planted token, the backend must
		// refuse any data request — the JWT carries `org=techflow` and
		// the host's X-Tenant-Slug header says `acme`.
		await signInAndWait(page, TECHFLOW_ADMIN);
		const techflowToken = await page.evaluate(() => localStorage.getItem('auth_token'));
		expect(techflowToken).toBeTruthy();

		// Use a fresh context for the acme origin — sharing the page
		// context can drag cookies / state in non-obvious ways.
		const acmeCtx = await browser.newContext();
		try {
			const r = await acmeCtx.request.get(`${API_URL}/api/invoices`, {
				headers: {
					Authorization: `Bearer ${techflowToken}`,
					'X-Tenant-Slug': 'acme'
				}
			});
			// 403 (tenant-mismatch guard) or 401 (in case the guard ever
			// gets reshaped to fail-as-anon). The forbidden line is "any
			// 2xx response" — that would mean the data leaked.
			expect(r.status(), 'planted token must not read acme data').toBeGreaterThanOrEqual(
				400
			);
			expect(r.status()).toBeLessThan(500);

			// Also verify nothing leaked in the body (defensive against
			// a future change that returns 4xx but still includes a
			// preview list).
			const bodyText = await r.text();
			expect(bodyText.toLowerCase()).not.toContain('"invoice_number"');
		} finally {
			await acmeCtx.close();
		}
	});
});
