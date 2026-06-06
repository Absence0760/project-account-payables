import { ACME_BASE, expect, test } from '../fixtures/helpers';
import { SERVICES, skipUnlessReachable } from '../fixtures/services';

/**
 * SSO login end-to-end through the real Keycloak IdP.
 *
 * Drives the whole OIDC handshake in the browser: /login → "Sign in with SSO" →
 * backend authorize (302 to Keycloak) → Keycloak login form → callback →
 * backend mints our JWT → tenant dashboard.
 *
 * Runs against the seeded `acme` tenant (where `pnpm idp:seed` enables SSO),
 * NOT the per-worker tenant. The Keycloak realm ships a `demo@acme.com` user
 * that links to acme's seeded admin, so a successful SSO login lands as that
 * admin.
 *
 * Gated: skips with an actionable message when Keycloak isn't up. Requires
 * `pnpm idp:up && pnpm idp:seed` locally; the CI e2e job starts Keycloak and
 * seeds acme before the suite runs.
 */

// Fresh unauthenticated browser, pinned to the acme origin (SSO lives on acme).
test.use({ baseURL: ACME_BASE, storageState: { cookies: [], origins: [] } });

test.describe('SSO login via Keycloak', () => {
	test.beforeEach(async () => {
		await skipUnlessReachable(SERVICES.keycloak);
	});

	test('the login page renders the SSO button when SSO is configured', async ({ page }) => {
		await page.goto('/login');
		await page.waitForLoadState('networkidle');
		await expect(page.locator('button.sso-btn')).toBeVisible();
		await expect(page.locator('button.sso-btn')).toContainText('Sign in with');
	});

	test('full OIDC handshake signs the user in and lands on the dashboard', async ({ page }) => {
		await page.goto('/login');
		await page.waitForLoadState('networkidle');

		// Click → window.location to backend /authorize → 302 to Keycloak.
		await page.locator('button.sso-btn').click();

		// Keycloak's hosted login form.
		await page.waitForURL(/\/realms\/account-payables\/protocol\/openid-connect\/auth/, {
			timeout: 15_000
		});
		await page.locator('#username').fill('demo@acme.com');
		await page.locator('#password').fill('demo');
		await page.locator('#kc-login').click();

		// Keycloak redirects to our callback, which POSTs code+state and, on
		// success, navigates to the tenant root (dashboard).
		await page.waitForURL(/^http:\/\/acme\.localhost:7777\/?$/, { timeout: 20_000 });

		// We're authenticated: a JWT is in localStorage and the app shell renders.
		const token = await page.evaluate(() => localStorage.getItem('auth_token'));
		expect(token).toBeTruthy();
		await expect(page.locator('.profile-btn')).toBeVisible();
	});
});
