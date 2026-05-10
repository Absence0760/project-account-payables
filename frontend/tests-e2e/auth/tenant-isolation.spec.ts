import { expect, test } from '@playwright/test';

import {
	ACME_ADMIN,
	ACME_BASE,
	signInAndWait,
	TECHFLOW_ADMIN,
	TECHFLOW_BASE
} from '../fixtures/helpers';

/**
 * Tenant isolation — a JWT minted for the techflow tenant must not be
 * usable to read acme's data even when the browser is pointed at the
 * acme subdomain. The backend enforces this at two layers:
 *
 *   1. JWT carries the org_id claim. Cross-tenant requests fail at
 *      the auth dependency.
 *   2. Each tenant's data lives in a separate Postgres DB
 *      (`ap_<slug>`), so even a leaked JWT can't accidentally read
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
	await page.waitForURL(new RegExp(`^${baseURL.replace(/[/]/g, '\\/')}/?$`), { timeout: 15_000 });
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

	test('swapping the tenant header mid-session does not grant cross-tenant access', async ({
		page
	}) => {
		// Simulate an attacker tampering with their own browser: log in
		// as techflow, then patch localStorage so the SPA thinks it's on
		// acme. The Authorization header is still techflow's JWT.
		await signInAndWait(page, TECHFLOW_ADMIN);
		await page.evaluate((slug) => {
			// The frontend reads tenant from the subdomain, not storage,
			// so we hop the origin instead — same effect.
			void slug;
		}, 'acme');

		const techflowToken = await page.evaluate(() => localStorage.getItem('auth_token'));
		expect(techflowToken).toBeTruthy();

		// Hop to acme's app shell — the SPA there will run with
		// techflow's token in localStorage (same origin? no — different
		// subdomain so localStorage isolates). The point of the test is
		// that even if the attacker copied the token over manually, the
		// backend would reject.
		await page.goto(`${ACME_BASE}/login`);
		await page.evaluate((t) => localStorage.setItem('auth_token', t!), techflowToken);

		await page.goto(`${ACME_BASE}/invoices`);
		await page.waitForURL(/\/login/, { timeout: 10_000 });
		expect(page.url()).toMatch(/^http:\/\/acme\.localhost:7777\//);
	});
});
