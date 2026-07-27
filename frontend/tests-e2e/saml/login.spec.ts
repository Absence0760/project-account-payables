import { ACME_BASE, expect, test } from '../fixtures/helpers';
import { SERVICES, skipUnlessReachable } from '../fixtures/services';

/**
 * SAML login end-to-end through the real Keycloak IdP.
 *
 * Drives the whole SAML SP-initiated handshake in the browser: /login → "Sign
 * in with SSO" → backend /auth/saml/login (302 to Keycloak) → Keycloak login
 * form → Keycloak POSTs the signed SAMLResponse to the backend ACS → backend
 * verifies the signature/conditions, mints our JWT, 303s to the SPA bridge with
 * a one-time code → the bridge exchanges it for the JWT → tenant dashboard.
 *
 * Runs against the seeded `acme` tenant (where `pnpm saml:seed` flips acme's
 * settings.sso to protocol=saml, replacing any OIDC block), NOT the per-worker
 * tenant. The Keycloak realm ships a `demo@acme.com` user that links to acme's
 * seeded admin, so a successful SAML login lands as that admin.
 *
 * Gated: skips with an actionable message when Keycloak/SAML isn't seeded.
 * Requires `pnpm idp:up && pnpm saml:seed` locally; the CI e2e job starts
 * Keycloak and seeds acme's SAML before the suite runs.
 */

// Fresh unauthenticated browser, pinned to the acme origin (SAML lives on acme).
test.use({ baseURL: ACME_BASE, storageState: { cookies: [], origins: [] } });

test.describe('SAML login via Keycloak', () => {
	test.beforeEach(async ({ page }) => {
		await skipUnlessReachable(SERVICES.keycloakSaml);
		// Pre-record the cookie-consent choice so the GDPR banner (fixed at the
		// bottom-centre of the viewport, z-index 10000) never intercepts the
		// SSO button on the vertically-centred login card. The banner is
		// orthogonal to the SAML handshake and has its own coverage in
		// consent-banner.spec.ts; mirrors sso/login.spec.ts.
		await page.addInitScript(() => {
			try {
				localStorage.setItem('feoh_consent_choice', 'accepted');
			} catch {
				/* about:blank — ignore */
			}
		});
	});

	test('the login page renders the SSO button when SAML is configured', async ({ page }) => {
		await page.goto('/login');
		await page.waitForLoadState('networkidle');
		await expect(page.locator('button.sso-btn')).toBeVisible();
		await expect(page.locator('button.sso-btn')).toContainText('Sign in with');
	});

	test('full SAML handshake signs the user in and lands on the dashboard', async ({ page }) => {
		await page.goto('/login');
		await page.waitForLoadState('networkidle');

		// Click → window.location to backend /auth/saml/login → 302 to Keycloak.
		await page.locator('button.sso-btn').click();

		// Keycloak's hosted SAML login form.
		await page.waitForURL(/\/realms\/account-payables\/protocol\/saml/, { timeout: 15_000 });
		await page.locator('#username').fill('demo@acme.com');
		await page.locator('#password').fill('demo');
		await page.locator('#kc-login').click();

		// Keycloak POSTs the SAMLResponse to the backend ACS, which verifies it,
		// mints our JWT, and (via the one-time-code bridge) lands on the tenant
		// root (dashboard).
		await page.waitForURL(/^http:\/\/acme\.localhost:7777\/?$/, { timeout: 20_000 });

		// We're authenticated: a JWT is in localStorage and the app shell renders.
		const token = await page.evaluate(() => localStorage.getItem('auth_token'));
		expect(token).toBeTruthy();
		await expect(page.locator('.profile-btn')).toBeVisible();
	});

	test('the JWT is never exposed in the callback URL', async ({ page }) => {
		await page.goto('/login');
		await page.waitForLoadState('networkidle');
		await page.locator('button.sso-btn').click();
		await page.waitForURL(/\/realms\/account-payables\/protocol\/saml/, { timeout: 15_000 });
		await page.locator('#username').fill('demo@acme.com');
		await page.locator('#password').fill('demo');

		// Capture the bridge URL the ACS 303s to — it must carry a one-time
		// ?code=, never the token itself (no #token=, no ?token=).
		const bridgeNav = page.waitForURL(/\/login\/saml-callback\?code=/, { timeout: 20_000 });
		await page.locator('#kc-login').click();
		await bridgeNav;
		const url = page.url();
		expect(url).toContain('/login/saml-callback?code=');
		expect(url).not.toContain('token');
	});
});
