import {
	API_BASE,
	authedTenantHeaders,
	currentTenantSlug,
	expect,
	signInAndWait,
	test
} from '../fixtures/helpers';

/**
 * /admin/api-keys — Developer-API key management (admin only).
 *
 * Surfaces the existing backend endpoints (`backend/app/api/api_keys.py`):
 *  - POST   /api/api-keys           → mint (plaintext key returned EXACTLY once)
 *  - GET    /api/api-keys           → list (prefix + metadata only)
 *  - DELETE /api/api-keys/{id}      → soft-revoke (idempotent)
 *  - GET    /api/api-keys/{id}/usage → per-key totals + per-day breakdown
 *
 * The plaintext key is shown once in a copy-able reveal and never echoed after.
 * Login model mirrors the suite: the per-worker storage state signs the admin
 * in (the only role the endpoints allow), so the page loads without a redirect.
 */

async function apiHeaders(page: import('@playwright/test').Page) {
	return {
		...(await authedTenantHeaders(page)),
		'Content-Type': 'application/json'
	};
}

interface ApiKeyResponse {
	id: string;
	name: string;
	key_prefix: string;
	scopes: string[];
	revoked_at: string | null;
}

/** Best-effort cleanup: revoke any key we minted in a test. */
async function revoke(page: import('@playwright/test').Page, id: string) {
	const headers = await apiHeaders(page);
	await page.request.delete(`${API_BASE}/api/api-keys/${id}`, { headers });
}

test.describe('/admin/api-keys (admin)', () => {
	// Deterministic explicit sign-in (don't lean on the shared storage cache) so
	// the gated page is reliably authed before each test.
	test.use({ storageState: { cookies: [], origins: [] } });

	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
	});

	test('mint shows the plaintext key once + lists the new key', async ({ page }) => {
		await page.goto('/admin/api-keys');
		await expect(page.getByRole('heading', { name: 'API Keys' })).toBeVisible();

		const name = `e2e-key-${Date.now()}`;

		// Open create modal + submit.
		await page.getByRole('button', { name: '+ Create key' }).click();
		const createModal = page.getByRole('dialog', { name: 'Create API key' });
		await expect(createModal).toBeVisible();
		await createModal.getByRole('textbox').first().fill(name);
		await createModal.getByRole('button', { name: 'Create' }).click();

		// The one-time reveal modal shows the FULL plaintext key (ap_live_… / a
		// long token), warns it's shown once, and offers a Copy button.
		const reveal = page.getByRole('dialog', { name: 'API key created' });
		await expect(reveal).toBeVisible({ timeout: 10_000 });
		const minted = reveal.getByTestId('minted-key');
		await expect(minted).toBeVisible();
		const plaintext = (await minted.textContent())?.trim() ?? '';
		// The plaintext is a real, long, prefixed key — not just the stored prefix.
		expect(plaintext.length).toBeGreaterThan(12);
		expect(plaintext.startsWith('ap_')).toBe(true);
		await expect(reveal.getByText(/shown only once/i)).toBeVisible();
		await expect(reveal.getByRole('button', { name: 'Copy' })).toBeVisible();

		// Dismiss the reveal — the plaintext must be gone (the surface never
		// re-shows it).
		await reveal.getByRole('button', { name: 'Done' }).click();
		await expect(reveal).toBeHidden();
		await expect(page.getByTestId('minted-key')).toHaveCount(0);

		// The new key is listed with its name + Active status + an Active row, but
		// NOT the plaintext (only the prefix).
		const row = page.locator('tr', { hasText: name });
		await expect(row).toBeVisible();
		await expect(row.getByText('Active')).toBeVisible();
		await expect(page.getByText(plaintext)).toHaveCount(0);

		// Cleanup via the API (resolve the id from the list).
		const headers = await apiHeaders(page);
		const list = (await (
			await page.request.get(`${API_BASE}/api/api-keys`, { headers })
		).json()) as ApiKeyResponse[];
		const created = list.find((k) => k.name === name);
		if (created) await revoke(page, created.id);
	});

	test('revoke disables the key (idempotent) and the row flips to Revoked', async ({ page }) => {
		const headers = await apiHeaders(page);
		const name = `e2e-revoke-${Date.now()}`;
		const created = (await (
			await page.request.post(`${API_BASE}/api/api-keys`, {
				headers,
				data: { name }
			})
		).json()) as { api_key: ApiKeyResponse; key: string };
		const id = created.api_key.id;

		await page.goto('/admin/api-keys');
		const row = page.locator('tr', { hasText: name });
		await expect(row).toBeVisible();
		await expect(row.getByText('Active')).toBeVisible();

		// Two-click armed revoke: first click arms ("Confirm"), second commits.
		// `exact` so the "View usage for e2e-revoke-…" row link (which contains
		// the substring "revoke") doesn't also match.
		await row.getByRole('button', { name: 'Revoke', exact: true }).click();
		await row.getByRole('button', { name: 'Confirm', exact: true }).click();

		// Row flips to Revoked; the Revoke action is gone for a revoked key.
		await expect(row.getByText('Revoked')).toBeVisible({ timeout: 10_000 });
		await expect(row.getByRole('button', { name: 'Revoke', exact: true })).toHaveCount(0);

		// Server-side: the key is revoked, and a repeat DELETE is idempotent (200,
		// no error).
		const after = (await (
			await page.request.get(`${API_BASE}/api/api-keys`, { headers })
		).json()) as ApiKeyResponse[];
		expect(after.find((k) => k.id === id)?.revoked_at).not.toBeNull();
		const repeat = await page.request.delete(`${API_BASE}/api/api-keys/${id}`, { headers });
		expect(repeat.ok()).toBe(true);
	});

	test('the per-key usage view renders totals + recent activity', async ({ page }) => {
		const headers = await apiHeaders(page);
		const name = `e2e-usage-${Date.now()}`;
		const created = (await (
			await page.request.post(`${API_BASE}/api/api-keys`, {
				headers,
				data: { name }
			})
		).json()) as { api_key: ApiKeyResponse };

		await page.goto('/admin/api-keys');
		const row = page.locator('tr', { hasText: name });
		await expect(row).toBeVisible();

		// Click the row's name link to open the usage modal.
		await row.getByRole('button', { name: `View usage for ${name}` }).click();

		const usageModal = page.getByRole('dialog', { name: 'API key usage' });
		await expect(usageModal).toBeVisible();
		await expect(usageModal.getByRole('heading', { name: `Usage — ${name}` })).toBeVisible();
		// Totals block renders (a brand-new key has 0 requests, but the totals
		// cards still show).
		await expect(usageModal.getByTestId('usage-totals')).toBeVisible({ timeout: 10_000 });
		await expect(usageModal.getByText('Total requests')).toBeVisible();
		await expect(usageModal.getByText(/Last 30 days/)).toBeVisible();

		await usageModal.getByRole('button', { name: 'Close' }).click();
		await expect(usageModal).toBeHidden();

		await revoke(page, created.api_key.id);
	});
});

test.describe('/admin/api-keys (clerk — not authorized)', () => {
	test.use({ storageState: { cookies: [], origins: [] } });

	test('ap_clerk is redirected away and the API 403s them', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);

		await page.goto('/admin/api-keys');
		// admin-only — the page waits for /me then bounces the clerk to root.
		await page.waitForURL(/^http:\/\/[^/]+:7777\/?$/, { timeout: 15_000 });
		await expect(page.getByRole('heading', { name: 'API Keys' })).toHaveCount(0);

		// The API itself 403s a non-admin.
		const token = await page.evaluate(() => localStorage.getItem('auth_token'));
		const resp = await page.request.get(`${API_BASE}/api/api-keys`, {
			headers: {
				Authorization: `Bearer ${token}`,
				'X-Tenant-Slug': currentTenantSlug()
			}
		});
		expect(resp.status()).toBe(403);
	});
});
