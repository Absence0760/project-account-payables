import { expect, test, ACME_BASE } from '../fixtures/helpers';

import { signInAndWait } from '../fixtures/helpers';

// Pinned to the acme tenant: this spec talks to acme directly
// (X-Tenant-Slug: 'acme' headers, ap_acme psql calls, hardcoded URLs).
// The per-worker baseURL from fixtures/helpers.ts would route to
// the wrong tenant. Multiple workers may share acme here — keep
// this file's tests read-only or idempotent.
test.use({ baseURL: ACME_BASE });

const API_BASE = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

async function authToken(page: import('@playwright/test').Page) {
	const t = await page.evaluate(() => localStorage.getItem('auth_token'));
	if (!t) throw new Error('not signed in');
	return t;
}

/**
 * /exceptions status-chip filtering. Seed has 3 open + 1 resolved
 * exception per tenant. Read-only — no mutations, no cleanup.
 */

test.describe('/exceptions status filter (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
		await page.goto('/exceptions');
		await page.waitForLoadState('networkidle');
	});

	test('Open chip is the default and shows only open rows', async ({ page }) => {
		await expect(page.locator('.filter-chip', { hasText: /^Open/ })).toHaveClass(/active/);

		const rows = page.locator('table tbody tr');
		expect(await rows.count()).toBeGreaterThan(0);
		const total = await rows.count();
		for (let i = 0; i < total; i++) {
			await expect(rows.nth(i).locator('.status-badge')).toHaveText(/open/i);
		}
	});

	test('Resolved chip narrows to resolved rows', async ({ page }) => {
		const filtered = page.waitForResponse(
			(r) => r.url().includes('/api/exceptions') && r.url().includes('status=resolved')
		);
		await page.locator('.filter-chip', { hasText: /^Resolved/ }).click();
		await filtered;

		await expect(page.locator('.filter-chip', { hasText: /^Resolved/ })).toHaveClass(/active/);
		const rows = page.locator('table tbody tr');
		const total = await rows.count();
		expect(total).toBeGreaterThan(0);
		for (let i = 0; i < total; i++) {
			await expect(rows.nth(i).locator('.status-badge')).toHaveText(/resolved/i);
		}
	});

	test('All chip clears the status filter and shows every row', async ({ page }) => {
		const token = await authToken(page);
		const all = await page.request.get(`${API_BASE}/api/exceptions`, {
			headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
		});
		const allBody = (await all.json()) as { items: unknown[] };
		const totalApi = allBody.items.length;

		// The frontend's "All" chip omits the status param entirely.
		const filtered = page.waitForResponse(
			(r) => r.url().includes('/api/exceptions?') && !r.url().includes('status=')
		);
		await page.locator('.filter-chip', { hasText: /^All/ }).click();
		await filtered;

		await expect(page.locator('.filter-chip', { hasText: /^All/ })).toHaveClass(/active/);
		await expect(page.locator('table tbody tr')).toHaveCount(totalApi);
	});

	test('Open count chip displays the seeded open total', async ({ page }) => {
		const token = await authToken(page);
		const summary = await page.request.get(`${API_BASE}/api/exceptions/summary`, {
			headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
		});
		const body = (await summary.json()) as { open: number };

		await expect(
			page.locator('.filter-chip', { hasText: /^Open/ }).locator('.count')
		).toHaveText(String(body.open));
	});
});
