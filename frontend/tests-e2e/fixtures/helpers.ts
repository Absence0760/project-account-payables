import { expect, type Page } from '@playwright/test';

/**
 * Seeded credentials, kept in lockstep with `backend/scripts/seed.py`.
 * Two tenants × roles — enough to assert role gates and tenant isolation.
 */
export const ACME_ADMIN = {
	email: 'demo@acme.com',
	password: 'demo'
} as const;

export const ACME_CLERK = {
	email: 'demo+apclerk@acme.com',
	password: 'demo'
} as const;

export const TECHFLOW_ADMIN = {
	email: 'admin@techflow.com',
	password: 'demo'
} as const;

/** Tenant origins. `*.localhost` resolves to 127.0.0.1 in Chromium. */
export const ACME_BASE = 'http://acme.localhost:7777';
export const TECHFLOW_BASE = 'http://techflow.localhost:7777';
export const NO_TENANT_BASE = 'http://localhost:7777';

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

/**
 * Sign in and wait for the post-login redirect to land on the tenant
 * root (`goto('/')` is what the login handler runs on success). Use
 * this when subsequent assertions need the authed app shell.
 */
export async function signInAndWait(
	page: Page,
	creds: { email: string; password: string } = ACME_ADMIN
) {
	await signIn(page, creds);
	// The tenant root is the dashboard. URL must end in just '/' — using
	// a trailing-slash regex anchors the match against descendant paths
	// like '/login/mfa'.
	await page.waitForURL(/^http:\/\/[^/]+:7777\/?$/, { timeout: 15_000 });
}

/**
 * Click the sidebar profile button → Log Out and assert the redirect
 * to /login.
 */
export async function signOut(page: Page) {
	await page.locator('.profile-btn').click();
	await page.locator('.profile-logout').click();
	await expect(page).toHaveURL(/\/login/);
}
