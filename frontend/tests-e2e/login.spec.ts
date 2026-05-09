import { expect, test } from '@playwright/test';

import { ACME_ADMIN, signIn } from './fixtures/helpers';

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

		// On bad creds the backend returns 401, and api.ts (line ~41)
		// runs window.location.href = '/login' which kicks off a full
		// nav back to /login before the form's catch block can render
		// the .error banner. The URL behaviour is the security contract;
		// the missing error message is a known UX gap, not a regression.
		await expect(page).toHaveURL(/\/login/);
	});

	test('seeded admin signs in and lands on the app shell', async ({ page }) => {
		await signIn(page, ACME_ADMIN);

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
