import { expect, type Page } from '@playwright/test';

import type { SeededUser } from './users';

/**
 * Drive the email-password sign-in form. Shared by globalSetup
 * (fixtures/auth.ts) and any spec that needs to assert sign-in
 * behaviour, so the hydration-wait + selector behaviour stays in one
 * place.
 *
 * Returns once the submit click has fired — caller is responsible for
 * asserting the destination URL if it cares.
 *
 * Adapt the selectors to whatever the project's actual login form
 * uses. The defaults below assume:
 *   - inputs typed `email` / `password`
 *   - a `<button type="submit">` inside the form
 *
 * Why the explicit `networkidle` wait: in many SPA frameworks
 * Playwright can click the submit button before the JS handler is
 * bound; with nothing preventing default, the form's native GET
 * submit fires and the page navigates to /login?email=...&password=...
 * — visually identical to "still on /login" but with no auth POST
 * attempted. Waiting for networkidle covers HMR + dynamic imports for
 * the auth module.
 */
export async function signIn(page: Page, user: SeededUser) {
	await page.goto('/login');
	await page.waitForLoadState('networkidle');

	await page.locator('input[type="email"]').fill(user.email);
	await page.locator('input[type="password"]').fill(user.password);
	await page.locator('form button[type="submit"]').click();
}

/**
 * Click through the navigation chrome's profile / account menu →
 * Sign out. Asserts the post-logout redirect to /login.
 *
 * Adapt the selector for whatever opens the menu in this project.
 */
export async function signOut(page: Page) {
	await page.locator('[data-testid="account-menu"]').click();
	await page.getByRole('button', { name: 'Sign out' }).click();
	await expect(page).toHaveURL(/\/login/);
}
