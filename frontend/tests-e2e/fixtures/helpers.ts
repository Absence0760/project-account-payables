import { type Page } from '@playwright/test';

/**
 * Seeded credentials, kept in lockstep with `backend/scripts/seed.py`.
 * The seed creates the `acme` tenant and four users with password
 * "demo"; we expose the admin role here. Add the others as specs need
 * them.
 */
export const ACME_ADMIN = {
	email: 'demo@acme.com',
	password: 'demo'
} as const;

/**
 * Drive the email-password sign-in form on the seeded `acme` tenant.
 * The frontend's tenant resolution requires hitting an `<slug>.localhost`
 * URL, so the playwright.config.ts baseURL is `acme.localhost:7777`.
 *
 * Returns once the submit click has fired. Callers assert the
 * destination URL.
 *
 * Why `waitForLoadState('networkidle')`: Svelte 5 binds the form's
 * `onsubmit` only after hydration. A click before that fires the
 * native GET submit, which navigates to /login?email=…&password=…
 * (visually identical to "still on /login" but with no auth POST
 * attempted). Waiting for networkidle covers Vite HMR + the dynamic
 * imports for `auth.svelte.ts` and `api.ts`.
 */
export async function signIn(
	page: Page,
	creds: { email: string; password: string } = ACME_ADMIN
) {
	await page.goto('/login');
	await page.waitForLoadState('networkidle');

	await page.locator('input[type="email"]').fill(creds.email);
	await page.locator('input[type="password"]').fill(creds.password);
	await page.locator('form button[type="submit"]').click();
}
