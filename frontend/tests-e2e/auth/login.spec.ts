import { expect, signIn, test } from '../fixtures/helpers';

// Start unauthenticated — this spec tests the login UI itself.
test.use({ storageState: { cookies: [], origins: [] } });

/**
 * Login surface — anonymous-visitor + happy-path smoke.
 *
 * baseURL is the current worker's per-tenant origin (the seeded
 * `e2e<N>` tenant). `localhost:7777` (no subdomain) is exercised
 * separately because it routes to the no-tenant Landing component,
 * not the login form.
 */

test.describe('/login', () => {
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
		await page.waitForURL(/^http:\/\/[^/]+:7777\/?$/, { timeout: 15_000 });
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
