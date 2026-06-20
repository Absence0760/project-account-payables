import {
	API_BASE,
	currentTenantSlug,
	expect,
	signInAndWait,
	test
} from '../fixtures/helpers';

/**
 * /admin/partner — Partner / reseller multi-tenant admin (admin only).
 *
 * Surfaces the backend `/api/partner` endpoints
 * (`backend/app/api/partner.py`):
 *  - GET /api/partner                              → the caller's child tenants
 *  - GET /api/partner/children/{id}/branding       → read a child's brand
 *  - PUT /api/partner/children/{id}/branding       → push a child's brand
 *
 * The surface is admin-only and scoped to the caller's own children. The e2e
 * seed tenants have no parent/child link, so the default is the "not a partner"
 * empty state — exactly what an unrelated org should see. These specs assert the
 * admin reaches the page (real backend GET) and a non-admin is bounced + 403'd.
 */

test.describe('/admin/partner (admin)', () => {
	// Deterministic explicit sign-in so the gated page is reliably authed.
	test.use({ storageState: { cookies: [], origins: [] } });

	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
	});

	test('admin loads the page; a standalone org shows the not-a-partner state', async ({
		page
	}) => {
		await page.goto('/admin/partner');
		await expect(page.getByRole('heading', { name: 'Partner Admin' })).toBeVisible();

		// The seed tenant administers no children, so the page resolves to the
		// empty "not a partner" state rather than a child table.
		await expect(page.getByTestId('partner-empty')).toBeVisible({ timeout: 10_000 });

		// The backend GET confirms the same: is_partner false, no children.
		const token = await page.evaluate(() => localStorage.getItem('auth_token'));
		const resp = await page.request.get(`${API_BASE}/api/partner`, {
			headers: {
				Authorization: `Bearer ${token}`,
				'X-Tenant-Slug': currentTenantSlug()
			}
		});
		expect(resp.status()).toBe(200);
		const body = (await resp.json()) as { is_partner: boolean; children: unknown[] };
		expect(body.is_partner).toBe(false);
		expect(body.children).toEqual([]);
	});

	test('a non-child org id is an opaque 404 (cross-tenant isolation)', async ({ page }) => {
		// Reading/pushing branding for an org this partner did NOT parent must be a
		// 404 — never a window into another tenant. A random uuid stands in for an
		// unrelated org id; it is the same opaque 404 as a real non-child.
		const token = await page.evaluate(() => localStorage.getItem('auth_token'));
		const headers = {
			Authorization: `Bearer ${token}`,
			'X-Tenant-Slug': currentTenantSlug(),
			'Content-Type': 'application/json'
		};
		const strangerId = '00000000-0000-0000-0000-000000000abc';
		const read = await page.request.get(
			`${API_BASE}/api/partner/children/${strangerId}/branding`,
			{ headers }
		);
		expect(read.status()).toBe(404);
		const write = await page.request.put(
			`${API_BASE}/api/partner/children/${strangerId}/branding`,
			{ headers, data: { product_name: 'Hijacked' } }
		);
		expect(write.status()).toBe(404);
	});
});

test.describe('/admin/partner (clerk — not authorized)', () => {
	test.use({ storageState: { cookies: [], origins: [] } });

	test('ap_clerk is redirected away and the API 403s them', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);

		await page.goto('/admin/partner');
		// admin-only — the page waits for /me then bounces the clerk to root.
		await page.waitForURL(/^http:\/\/[^/]+:7777\/?$/, { timeout: 15_000 });
		await expect(page.getByRole('heading', { name: 'Partner Admin' })).toHaveCount(0);

		// The API itself 403s a non-admin.
		const token = await page.evaluate(() => localStorage.getItem('auth_token'));
		const resp = await page.request.get(`${API_BASE}/api/partner`, {
			headers: {
				Authorization: `Bearer ${token}`,
				'X-Tenant-Slug': currentTenantSlug()
			}
		});
		expect(resp.status()).toBe(403);
	});
});
