import { API_BASE, authedTenantHeaders, expect, signInAndWait, test } from '../fixtures/helpers';

/**
 * Supplier-portal white-label theming.
 *
 * The portal (`/portal/*`) is a SEPARATE surface (VendorUser auth, its own
 * `portal_auth_token` key). It must carry the SAME per-tenant brand the main app
 * does — accent colors + logo + product name + <title> — including on the
 * UNAUTHENTICATED login page. The brand source is the public-by-design
 * `GET /api/portal/branding` (resolved by the tenant header/Host, returns only
 * the non-sensitive BrandConfig fields), applied by `portalBrand` in the portal
 * layout.
 *
 * This spec sets a known brand via the admin `PUT /api/organization/branding`
 * (the employee write path) and then asserts the portal applies it. The portal
 * uses its own token, so an employee session on the page is irrelevant to the
 * portal's auth — we drive the portal login (anon to the portal) directly.
 *
 * Cleanup restores the brand to empty in `afterAll` so the worker's tenant is
 * left as found and other specs aren't tinted.
 */

const ACCENT = '#a1b2c3';
const ACCENT_STRONG = '#102030';
const PRODUCT_NAME = 'E2E Portal Brand';
const LOGO_URL = 'https://cdn.e2e.test/portal-logo.png';

const EMPTY_BRAND = {
	product_name: '',
	logo_url: '',
	accent_color: '',
	accent_strong_color: '',
	support_url: '',
	legal_url: ''
};

async function putBranding(
	page: import('@playwright/test').Page,
	brand: Record<string, string>
): Promise<void> {
	const resp = await page.request.put(`${API_BASE}/api/organization/branding`, {
		headers: await authedTenantHeaders(page),
		data: brand
	});
	expect(resp.ok()).toBeTruthy();
}

test.describe('supplier-portal white-label theming', () => {
	test.beforeEach(async ({ page }) => {
		// Sign in as the worker's tenant admin so we hold a token to write the
		// brand. (The portal reads it back over the PUBLIC endpoint regardless.)
		await signInAndWait(page);
		await putBranding(page, {
			product_name: PRODUCT_NAME,
			logo_url: LOGO_URL,
			accent_color: ACCENT,
			accent_strong_color: ACCENT_STRONG,
			support_url: '',
			legal_url: ''
		});
	});

	test.afterEach(async ({ page }) => {
		// Best-effort restore. The page is already authed from beforeEach.
		await putBranding(page, EMPTY_BRAND).catch(() => {});
	});

	test('portal login applies the tenant accent + logo + product name', async ({ page }) => {
		// Portal login is anon to the PORTAL (its own token key), so it must theme
		// purely from the public brand read.
		await page.goto('/portal/login');
		await page.waitForLoadState('networkidle');

		// The accent CSS custom property is written onto <html> by portalBrand.
		await expect
			.poll(async () =>
				page.evaluate(() =>
					document.documentElement.style.getPropertyValue('--accent').trim()
				)
			)
			.toBe(ACCENT);
		expect(
			await page.evaluate(() =>
				document.documentElement.style.getPropertyValue('--accent-strong').trim()
			)
		).toBe(ACCENT_STRONG);

		// The product name is the login card heading + drives <title>.
		await expect(page.getByRole('heading', { name: PRODUCT_NAME })).toBeVisible();
		await expect(page).toHaveTitle(new RegExp(PRODUCT_NAME));

		// The tenant logo renders.
		await expect(page.locator(`img[src="${LOGO_URL}"]`).first()).toBeVisible();
	});
});
