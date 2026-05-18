import { expect, test, ACME_BASE } from '../fixtures/helpers';

import { ACME_ADMIN, signIn } from '../fixtures/helpers';

// Pinned to the acme tenant: this spec uses ACME_*/TECHFLOW_* creds or
// asserts cross-tenant isolation that requires fixed tenant slugs. The
// per-worker baseURL from fixtures/helpers.ts would otherwise route to
// the wrong tenant. Multiple workers may share acme here — keep this
// file's tests read-only or idempotent.
test.use({ baseURL: ACME_BASE });

/**
 * Login surface — anonymous-visitor + happy-path smoke.
 *
 * baseURL is `http://acme.localhost:7777` (the seeded tenant).
 * `localhost:7777` (no subdomain) is exercised separately because it
 * routes to the no-tenant Landing component, not the login form.
 */

test.describe('/login (acme tenant)', () => {
	test('renders the sign-in form for an anon visitor', async ({ page }) => {
		await page.goto('/login');
		await page.waitForLoadState('networkidle');

		await expect(page.getByRole('heading', { name: 'Account Payables' })).toBeVisible();
		await expect(page.locator('input[type="email"]')).toBeVisible();
		await expect(page.locator('input[type="password"]')).toBeVisible();
		await expect(page.getByRole('button', { name: /Sign in/ })).toBeVisible();
	});

	test('rejects an unknown email/password and stays on /login', async ({ page }) => {
		await signIn(page, { email: 'noone@nowhere.test', password: 'wrong-password' });

		// Stay on /login (the form re-renders with an error banner).
		// We don't assert on the error copy — backend wording may
		// shift; the URL behaviour is the security contract and the
		// banner is the UX contract.
		await expect(page).toHaveURL(/\/login/);
		await expect(page.locator('.error')).toBeVisible();
	});

	test('seeded admin signs in and lands on the app shell', async ({ page }) => {
		await signIn(page);

		// Successful login calls `goto('/')`. The exact landing chrome
		// is the dashboard for an authenticated tenant user — assert on
		// the URL transition, not on dashboard internals (those evolve).
		await page.waitForURL(/^http:\/\/acme\.localhost:7777\/?$/, { timeout: 15_000 });
	});
});

test.describe('/ (no-tenant landing)', () => {
	// Override the tenant subdomain in the baseURL — the marketing
	// landing only renders when the request has no tenant slug.
	test.use({ baseURL: 'http://localhost:7777' });

	test('shows the marketing landing, not the login form', async ({ page }) => {
		await page.goto('/');
		await page.waitForLoadState('networkidle');

		// Login form should NOT be present on the no-tenant route.
		await expect(page.locator('input[type="password"]')).toHaveCount(0);
	});
});
