import { expect, test } from '../fixtures/helpers';

import { signOut } from '../fixtures/helpers';

/**
 * Sign-out flow + post-logout protection. The two assertions together
 * are the contract: "Log Out clears the session, and the cleared
 * session means a re-visit to a protected route bounces to /login."
 */

test.describe('sign out (acme admin)', () => {
	test('Log Out clears the session and lands on /login', async ({ page }) => {
		// Land on the dashboard so the sidebar (with the profile button) renders.
		await page.goto('/');
		await signOut(page);

		// auth_token is the localStorage key cleared by auth.logout().
		// Reaching this assertion proves the sidebar's profile-popover
		// path executed clearToken() and rerouted us.
		const token = await page.evaluate(() => localStorage.getItem('auth_token'));
		expect(token).toBeNull();
	});

	test('after logout, /invoices bounces back to /login', async ({ page }) => {
		await page.goto('/');
		await signOut(page);

		await page.goto('/invoices');
		await page.waitForURL(/\/login$/, { timeout: 5_000 });
	});
});
