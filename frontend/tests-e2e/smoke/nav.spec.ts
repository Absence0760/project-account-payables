import { expect, test } from '../fixtures/helpers';

import { signInAndWait } from '../fixtures/helpers';

/**
 * Navigation smoke — a signed-in admin can reach every page the
 * sidebar offers, and each route renders without an unhandled error
 * (no crash, no redirect back to /login).
 *
 * The admin role is unrestricted, so this hits every route at once.
 * Per-role visibility (clerks don't see /admin, /workflows, etc.) is a
 * separate spec when we want it.
 */

const ROUTES = [
	{ path: '/', anchor: 'aside.sidebar' },
	{ path: '/invoices', anchor: '.filter-row input[placeholder="Search invoices..." i]' },
	{ path: '/payments', anchor: 'aside.sidebar' },
	{ path: '/vendors', anchor: 'aside.sidebar' },
	{ path: '/exceptions', anchor: 'aside.sidebar' },
	{ path: '/workflows', anchor: 'aside.sidebar' },
	{ path: '/organization', anchor: 'aside.sidebar' },
	{ path: '/admin', anchor: 'aside.sidebar' }
] as const;

test.describe('signed-in nav (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
	});

	for (const { path, anchor } of ROUTES) {
		test(`reaches ${path}`, async ({ page }) => {
			await page.goto(path);
			// Sidebar visible = inside the app shell (the +layout.svelte
			// `auth.loggedIn && auth.user && !must_change_password` branch).
			// Anything narrower (page-specific selectors) gets covered by
			// per-page specs as we write them.
			await expect(page.locator(anchor).first()).toBeVisible();

			// Hardening: should NOT have been bounced to /login during
			// the navigation. A regression where a route accidentally
			// gates on a stricter role than the admin satisfies would
			// surface here.
			await expect(page).not.toHaveURL(/\/login/);
		});
	}
});
