import { expect, test } from './fixtures/helpers';

/**
 * Consent banner — GDPR/ePrivacy + CCPA non-essential-storage consent.
 *
 * Mounted once in `routes/+layout.svelte` (outside the routed slot) so it
 * covers the app shell, marketing landing, signup, and supplier portal. It
 * shows on first visit and hides once a choice is recorded in `localStorage`
 * under `feoh_consent_choice`.
 *
 * The default per-worker storage state signs the admin in (an `auth_token`
 * in localStorage) but never sets the consent key, so the banner still
 * appears. Each test starts from a known-fresh consent state by removing the
 * key before reload — no waitForTimeout / sleeps; we wait on real DOM signals
 * (the banner region appearing / detaching) and on the persisted value.
 */

const CONSENT_KEY = 'feoh_consent_choice';
const banner = (page) => page.getByRole('region', { name: 'Cookie and privacy consent' });

test.describe('consent banner', () => {
	test.beforeEach(async ({ page }) => {
		// Land on the app, then clear any prior consent choice and reload so the
		// banner reliably shows for this test (auth token is left intact).
		await page.goto('/');
		await page.evaluate((key) => localStorage.removeItem(key), CONSENT_KEY);
		await page.reload();
	});

	test('shows on first visit with the three choices', async ({ page }) => {
		await expect(banner(page)).toBeVisible();
		await expect(page.getByRole('button', { name: 'Accept all' })).toBeVisible();
		await expect(page.getByRole('button', { name: 'Reject non-essential' })).toBeVisible();
		await expect(page.getByRole('button', { name: 'Manage' })).toBeVisible();
	});

	test('Manage reveals the per-category details', async ({ page }) => {
		await expect(banner(page)).toBeVisible();
		await page.getByRole('button', { name: 'Manage' }).click();
		await expect(banner(page).getByText('Strictly necessary (always on)')).toBeVisible();
		await expect(banner(page).getByText('Analytics (optional)')).toBeVisible();
	});

	test('Accept persists the choice and hides the banner', async ({ page }) => {
		await expect(banner(page)).toBeVisible();
		await page.getByRole('button', { name: 'Accept all' }).click();

		// Banner detaches (the `{#if visible}` block leaves the DOM).
		await expect(banner(page)).toHaveCount(0);

		// Choice is persisted...
		const stored = await page.evaluate((key) => localStorage.getItem(key), CONSENT_KEY);
		expect(stored).toBe('accepted');

		// ...and survives a reload (banner stays hidden).
		await page.reload();
		await expect(banner(page)).toHaveCount(0);
	});

	test('Reject persists the choice and hides the banner', async ({ page }) => {
		await expect(banner(page)).toBeVisible();
		await page.getByRole('button', { name: 'Reject non-essential' }).click();

		await expect(banner(page)).toHaveCount(0);

		const stored = await page.evaluate((key) => localStorage.getItem(key), CONSENT_KEY);
		expect(stored).toBe('rejected');

		await page.reload();
		await expect(banner(page)).toHaveCount(0);
	});
});
