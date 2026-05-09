import { expect, test } from '@playwright/test';

import { signIn } from './fixtures/helpers';
import { ADMIN_A } from './fixtures/users';

/**
 * /login — auth surface.
 *
 * Successful-sign-in is exercised by globalSetup (fixtures/auth.ts)
 * for every seeded user. This file holds the /login-only behaviours:
 * failed sign-ins that stay on /login, the page rendering for an
 * anon visitor, and (in future) reset-password / OAuth affordances.
 *
 * Use this as the template when the project lands on a real login
 * form. Adapt selectors and URL patterns to match.
 */

test.describe('/login', () => {
	// Override globalSetup's storage state — this suite tests the
	// unauthenticated entry point.
	test.use({ storageState: { cookies: [], origins: [] } });

	test('rejects an unknown email/password combo and stays on /login', async ({
		page
	}) => {
		await signIn(page, {
			...ADMIN_A,
			email: 'noone@nowhere.test',
			password: 'wrong-password'
		});

		// Stay on /login (the form re-renders with an error banner).
		// We don't assert the error copy — it may shift; the URL
		// behaviour is the security contract.
		await expect(page).toHaveURL(/\/login/);
	});

	test('renders the sign-in form for an anon visitor', async ({ page }) => {
		await page.goto('/login');
		await page.waitForLoadState('networkidle');

		await expect(page.locator('input[type="email"]')).toBeVisible();
		await expect(page.locator('input[type="password"]')).toBeVisible();
		await expect(page.locator('form button[type="submit"]')).toBeVisible();
	});
});
