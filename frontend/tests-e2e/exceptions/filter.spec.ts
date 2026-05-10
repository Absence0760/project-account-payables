import { expect, test } from '@playwright/test';

import { signInAndWait } from '../fixtures/helpers';

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

	test('Open chip is the default and shows only open cards', async ({ page }) => {
		await expect(page.locator('.filter-chip', { hasText: /^Open/ })).toHaveClass(/active/);

		// All visible cards' status badge reads "open".
		const cards = page.locator('.exception-card');
		expect(await cards.count()).toBeGreaterThan(0);
		const total = await cards.count();
		for (let i = 0; i < total; i++) {
			await expect(cards.nth(i).locator('.exc-status')).toHaveText(/open/i);
		}
	});

	test('Resolved chip narrows to resolved cards', async ({ page }) => {
		const filtered = page.waitForResponse(
			(r) => r.url().includes('/api/exceptions') && r.url().includes('status=resolved')
		);
		await page.locator('.filter-chip', { hasText: /^Resolved/ }).click();
		await filtered;

		await expect(page.locator('.filter-chip', { hasText: /^Resolved/ })).toHaveClass(/active/);
		const cards = page.locator('.exception-card');
		const total = await cards.count();
		// Seed has at least 1 resolved per tenant.
		expect(total).toBeGreaterThan(0);
		for (let i = 0; i < total; i++) {
			await expect(cards.nth(i).locator('.exc-status')).toHaveText(/resolved/i);
		}
	});

	test('All chip clears the status filter and shows every card', async ({ page }) => {
		const token = await authToken(page);
		const all = await page.request.get(`${API_BASE}/api/exceptions`, {
			headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
		});
		const allBody = (await all.json()) as { items: unknown[] };
		const totalApi = allBody.items.length;

		// The frontend's "All" chip omits the status param entirely.
		const filtered = page.waitForResponse(
			(r) =>
				r.url().includes('/api/exceptions?') && !r.url().includes('status=')
		);
		await page.locator('.filter-chip', { hasText: /^All/ }).click();
		await filtered;

		await expect(page.locator('.filter-chip', { hasText: /^All/ })).toHaveClass(/active/);
		await expect(page.locator('.exception-card')).toHaveCount(totalApi);
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
