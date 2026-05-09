import { expect, test } from '@playwright/test';

import { signInAndWait, signOut } from './fixtures/helpers';

/**
 * Sign-out flow + post-logout protection. The two assertions together
 * are the contract: "Log Out clears the session, and the cleared
 * session means a re-visit to a protected route bounces to /login."
 */

test.describe('sign out (acme admin)', () => {
	test('Log Out clears the session and lands on /login', async ({ page }) => {
		await signInAndWait(page);
		await signOut(page);

		// auth_token is the localStorage key cleared by auth.logout().
		// Reaching this assertion proves the sidebar's profile-popover
		// path executed clearToken() and rerouted us.
		const token = await page.evaluate(() => localStorage.getItem('auth_token'));
		expect(token).toBeNull();
	});

	test('after logout, /invoices bounces back to /login', async ({ page }) => {
		await signInAndWait(page);
		await signOut(page);

		await page.goto('/invoices');
		await page.waitForURL(/\/login$/, { timeout: 5_000 });
	});
});
