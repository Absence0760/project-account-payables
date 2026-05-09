import type { FullConfig } from '@playwright/test';
import { chromium } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';

import { signIn } from './helpers';
import { ALL_USERS, type SeededUser } from './users';

/**
 * Playwright globalSetup — sign each seeded user in once via the UI
 * and save the storage state to disk.
 *
 * Spec files then attach the storage state via:
 *
 *   test.use({ storageState: ADMIN_A.storageStatePath });
 *
 * to skip the form submit on every test. The first-time login is the
 * only place a real `/login` form interaction happens; everything
 * downstream rides on the persisted session cookie.
 *
 * We deliberately use the UI (not a direct API call) so this fixture
 * exercises the actual sign-in path once per CI run. If sign-in is
 * broken, every spec fails fast in globalSetup with a clear "could
 * not sign in user A" rather than cascading into N confusing 401-
 * from-the-app failures.
 */
export default async function globalSetup(config: FullConfig) {
	const baseURL =
		config.projects[0]?.use?.baseURL ?? 'http://localhost:5173';

	for (const user of ALL_USERS) {
		await signInAndSaveState(baseURL, user);
	}
}

async function signInAndSaveState(baseURL: string, user: SeededUser) {
	const browser = await chromium.launch();
	const ctx = await browser.newContext({ baseURL });
	const page = await ctx.newPage();

	try {
		await signIn(page, user);

		// Wait for the post-login navigation to settle. Adapt the URL
		// pattern to wherever this project redirects after a successful
		// sign-in.
		try {
			await page.waitForURL(/\/(dashboard|invoices|home)$/, { timeout: 10_000 });
		} catch (err) {
			if (process.env.E2E_SKIP_AUTH_FAILURES) {
				console.warn(
					`[auth fixture] Sign-in for ${user.email} did not redirect — final URL ${page.url()}. ` +
						`Skipping due to E2E_SKIP_AUTH_FAILURES.`
				);
				return;
			}
			const errorText = await page
				.locator('.error-banner, .alert-error, [role="alert"]')
				.textContent()
				.catch(() => '<no error banner>');
			throw new Error(
				`auth fixture: ${user.email} did not redirect after sign-in. ` +
					`Final URL: ${page.url()}. Error banner: "${errorText}".`
			);
		}

		// storageStatePath is already absolute (resolved at module-load
		// in users.ts).
		await mkdir(dirname(user.storageStatePath), { recursive: true });
		await ctx.storageState({ path: user.storageStatePath });
	} finally {
		await ctx.close();
		await browser.close();
	}
}
