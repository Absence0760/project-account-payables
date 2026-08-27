import { expect, test } from '../fixtures/helpers';

// Start unauthenticated — these specs test the forgot/reset-password UI,
// same reason tests-e2e/auth/login.spec.ts opts out of the preloaded admin
// storage state.
test.use({ storageState: { cookies: [], origins: [] } });

/**
 * Forgot/reset password UI (`/login/forgot-password`, `/login/reset-password`).
 *
 * Deliberately does NOT exercise the full request -> email -> redeem round
 * trip here — that would either mutate a real seeded tenant credential (the
 * `demo+<role>@<slug>.localhost` accounts other specs and workers depend on
 * staying at their seed password) or require intercepting the console email
 * adapter's output, neither of which belongs in a UI smoke spec. The full
 * round trip (including the seeded-password mutation risk) is covered
 * end-to-end against real Postgres + Redis in
 * `backend/tests/test_forgot_reset_password.py`, which mints its own
 * throwaway control-plane user precisely to avoid that hazard. This spec
 * only proves the UI is wired to the right endpoints and states.
 */

test.describe('/login/forgot-password', () => {
	test('is reachable from the login page', async ({ page }) => {
		await page.goto('/login');
		await page.waitForLoadState('networkidle');

		const link = page.getByRole('link', { name: /Forgot password/i });
		await expect(link).toBeVisible();
		await link.click();

		await expect(page).toHaveURL(/\/login\/forgot-password$/);
		await expect(page.getByRole('heading', { name: /Reset your password/i })).toBeVisible();
	});

	test('shows the generic success message regardless of whether the email exists', async ({
		page
	}) => {
		await page.goto('/login/forgot-password');
		await page.waitForLoadState('networkidle');

		await page.locator('input[type="email"]').fill(`nobody-${Date.now()}@nowhere.example`);
		await page.getByRole('button', { name: /Send reset link/i }).click();

		// Same success copy whether or not the address matched an account —
		// the enumeration-resistance contract backend/app/api/auth.py
		// ::forgot_password documents.
		await expect(page.getByText(/we've sent a link to reset your password/i)).toBeVisible();
		await expect(page.getByRole('link', { name: /Back to sign in/i })).toBeVisible();
	});
});

test.describe('/login/reset-password', () => {
	test('flags a missing token instead of rendering a form', async ({ page }) => {
		await page.goto('/login/reset-password');
		await page.waitForLoadState('networkidle');

		await expect(page.locator('input[type="password"]')).toHaveCount(0);
		await expect(page.getByText(/missing its token/i)).toBeVisible();
	});

	test('rejects a bogus token with the opaque invalid/expired message', async ({ page }) => {
		await page.goto('/login/reset-password?token=not-a-real-token');
		await page.waitForLoadState('networkidle');

		await page.locator('input[type="password"]').first().fill('BrandNewPassw0rd!42');
		await page.locator('input[type="password"]').nth(1).fill('BrandNewPassw0rd!42');
		await page.getByRole('button', { name: /Reset password/i }).click();

		// The backend's actual detail ("Invalid or expired reset link.",
		// app/api/auth.py) is what renders here — the frontend's
		// auth.resetPassword.invalidToken i18n string is only the fallback
		// for a non-Error/network failure, a different path than this test
		// exercises.
		await expect(page.getByText(/invalid or expired/i)).toBeVisible();
	});
});
