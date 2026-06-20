import { API_BASE, currentTenantSlug, expect, signInAndWait, test } from '../fixtures/helpers';

/**
 * /admin/partner — link provisioning (attach / detach) surface.
 *
 * Backend `/api/partner` provisioning endpoints (`backend/app/api/partner.py`):
 *  - POST /api/partner/link-code        → the caller mints a single-use code so a
 *                                         partner can attach it AS a child (consent)
 *  - POST /api/partner/children         → redeem a child-issued code to attach it
 *  - DELETE /api/partner/children/{id}   → detach a child (back to standalone)
 *
 * The authorization model is TWO-SIDED CONSENT: a partner can only attach an org
 * that minted a link code. These specs assert (a) the admin UI exposes the
 * mint + attach affordances and minting returns a code, and (b) the privilege
 * boundary — attaching a garbage / non-consenting code is rejected so no
 * cross-tenant link is created. The full happy-path round-trip is covered
 * exhaustively by the backend pytest suite (real Postgres, two tenants).
 */

test.describe('/admin/partner provisioning (admin)', () => {
	test.use({ storageState: { cookies: [], origins: [] } });

	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
	});

	test('admin sees the attach + link-code affordances and can mint a code', async ({ page }) => {
		await page.goto('/admin/partner');
		await expect(page.getByRole('heading', { name: 'Partner Admin' })).toBeVisible();

		// The new provisioning affordances are present.
		await expect(page.getByTestId('attach-child-btn')).toBeVisible({ timeout: 10_000 });
		await expect(page.getByTestId('link-code-panel')).toBeVisible();

		// Minting a code (this workspace consenting to be a child) surfaces an
		// opaque code in the UI — the dev signing key is set in .env.development.
		await page.getByRole('button', { name: 'Generate link code' }).click();
		const minted = page.getByTestId('minted-link-code');
		await expect(minted).toBeVisible({ timeout: 10_000 });
		await expect(minted.locator('.code-value')).not.toBeEmpty();
	});

	test('attaching a non-consenting (garbage) code is rejected — no link created', async ({
		page
	}) => {
		// The privilege boundary: without a valid child-issued code, a partner
		// cannot adopt anyone. A forged/garbage code is an opaque 400.
		const token = await page.evaluate(() => localStorage.getItem('auth_token'));
		const resp = await page.request.post(`${API_BASE}/api/partner/children`, {
			headers: {
				Authorization: `Bearer ${token}`,
				'X-Tenant-Slug': currentTenantSlug(),
				'Content-Type': 'application/json'
			},
			data: { link_code: 'totally.bogus' }
		});
		expect(resp.status()).toBe(400);

		// The caller still administers no children (no silent adoption happened).
		const overview = await page.request.get(`${API_BASE}/api/partner`, {
			headers: {
				Authorization: `Bearer ${token}`,
				'X-Tenant-Slug': currentTenantSlug()
			}
		});
		expect(overview.status()).toBe(200);
		const body = (await overview.json()) as { children: unknown[] };
		expect(body.children).toEqual([]);
	});

	test('a self-minted code cannot attach the workspace to itself', async ({ page }) => {
		// Mint a code for THIS org, then try to redeem it as the same org. A
		// self-FK loop is nonsensical — the backend rejects it with a 400.
		const token = await page.evaluate(() => localStorage.getItem('auth_token'));
		const headers = {
			Authorization: `Bearer ${token}`,
			'X-Tenant-Slug': currentTenantSlug(),
			'Content-Type': 'application/json'
		};
		const mint = await page.request.post(`${API_BASE}/api/partner/link-code`, {
			headers,
			data: {}
		});
		expect(mint.status()).toBe(200);
		const { link_code } = (await mint.json()) as { link_code: string };

		const attach = await page.request.post(`${API_BASE}/api/partner/children`, {
			headers,
			data: { link_code }
		});
		expect(attach.status()).toBe(400);
	});
});

test.describe('/admin/partner provisioning (clerk — not authorized)', () => {
	test.use({ storageState: { cookies: [], origins: [] } });

	test('a clerk cannot mint a link code or attach a child (403)', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);
		const token = await page.evaluate(() => localStorage.getItem('auth_token'));
		const headers = {
			Authorization: `Bearer ${token}`,
			'X-Tenant-Slug': currentTenantSlug(),
			'Content-Type': 'application/json'
		};
		const mint = await page.request.post(`${API_BASE}/api/partner/link-code`, {
			headers,
			data: {}
		});
		expect(mint.status()).toBe(403);
		const attach = await page.request.post(`${API_BASE}/api/partner/children`, {
			headers,
			data: { link_code: 'x.y' }
		});
		expect(attach.status()).toBe(403);
	});
});
