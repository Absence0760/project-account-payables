import { expect, test } from '@playwright/test';

import { ACME_BASE, signInAndWait, TECHFLOW_ADMIN, TECHFLOW_BASE } from './fixtures/helpers';

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
 * The e2e proxy for that contract is: sign in on techflow.localhost,
 * then navigate to acme.localhost. The acme app shell sends the
 * cookie + tenant header `acme` to the backend, but the JWT's org_id
 * is for techflow. The backend rejects → api.ts (line ~41) catches
 * the 401 → window.location.href = '/login' → user lands on acme's
 * /login page, NOT the acme dashboard.
 */

test.describe('tenant isolation', () => {
	// Override baseURL so signIn navigates to techflow's /login.
	test.use({ baseURL: TECHFLOW_BASE });

	test('techflow JWT cannot reach acme data', async ({ page }) => {
		await signInAndWait(page, TECHFLOW_ADMIN);

		// The auth_token in localStorage is now techflow's JWT. Hop to
		// acme's invoices route. The frontend sends X-Tenant-Slug=acme
		// (subdomain) with the (techflow) Bearer token. Backend rejects.
		// api.ts auto-redirects to /login on 401. End state: anon on
		// acme's /login, not acme's invoices page.
		await page.goto(`${ACME_BASE}/invoices`);
		await page.waitForURL(/\/login/, { timeout: 10_000 });

		// Confirm we're on acme.localhost, not techflow.localhost — this
		// is what proves the cross-tenant attempt actually happened (vs
		// the test simply never leaving techflow).
		expect(page.url()).toMatch(/^http:\/\/acme\.localhost:7777\//);
	});
});
