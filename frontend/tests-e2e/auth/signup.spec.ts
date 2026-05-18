import { expect, test } from '../fixtures/helpers';

import { NO_TENANT_BASE } from '../fixtures/helpers';

/**
 * Self-service signup — anonymous-tenant route at /signup. Renders only
 * on the root domain (no tenant subdomain), so we override baseURL to
 * the no-tenant origin.
 */

test.describe('/signup (no tenant)', () => {
	test.use({ baseURL: NO_TENANT_BASE });

	test('renders the create-workspace form', async ({ page }) => {
		await page.goto('/signup');
		await page.waitForLoadState('networkidle');

		await expect(
			page.getByRole('heading', { name: 'Create your workspace' })
		).toBeVisible();
		await expect(page.getByPlaceholder('acme')).toBeVisible();
		await expect(
			page.getByRole('button', { name: 'Send verification email' })
		).toBeVisible();
	});

	test('slug-check rejects an already-taken slug', async ({ page }) => {
		await page.goto('/signup');
		await page.waitForLoadState('networkidle');

		// `acme` is seeded — slug-check should mark it unavailable.
		// The page shows a `.hint.bad` with the rejection reason; the
		// submit button also goes disabled when slugStatus === 'bad'.
		await page.getByPlaceholder('acme').fill('acme');

		await expect(page.locator('small.hint.bad')).toBeVisible({ timeout: 5_000 });
		await expect(
			page.getByRole('button', { name: 'Send verification email' })
		).toBeDisabled();
	});

	test('slug-check accepts a fresh slug', async ({ page }) => {
		await page.goto('/signup');
		await page.waitForLoadState('networkidle');

		// Time-suffixed slug ensures it's never been used (avoids
		// inter-run pollution if the DB persists between local dev runs).
		const fresh = `e2e${Date.now().toString().slice(-8)}`;
		await page.getByPlaceholder('acme').fill(fresh);

		// `.hint.ok` ("Available") appears once the debounced check
		// returns 200 from /api/signup/slug-check.
		await expect(page.locator('small.hint.ok')).toBeVisible({ timeout: 5_000 });
	});
});
