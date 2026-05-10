import { expect, test } from '@playwright/test';

import { signInAndWait } from '../fixtures/helpers';

const API_BASE = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

async function authToken(page: import('@playwright/test').Page) {
	const t = await page.evaluate(() => localStorage.getItem('auth_token'));
	if (!t) throw new Error('not signed in');
	return t;
}

async function findVendorByStatus(
	page: import('@playwright/test').Page,
	wanted: 'active' | 'unverified' | 'rejected'
) {
	const token = await authToken(page);
	const resp = await page.request.get(
		`${API_BASE}/api/vendors?status=${wanted}&page_size=100`,
		{ headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' } }
	);
	const body = (await resp.json()) as {
		items: Array<{ id: string; name: string; status: string }>;
	};
	return body.items.find((v) => v.status === wanted) ?? null;
}

async function setVendorStatus(
	page: import('@playwright/test').Page,
	id: string,
	status: 'active' | 'unverified' | 'rejected'
) {
	const token = await authToken(page);
	await page.request.patch(`${API_BASE}/api/vendors/${id}`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
		data: { status }
	});
}

/**
 * Vendor verify/reject lifecycle. The seed has at least one
 * `unverified` vendor; the row's actions cell shows Verify + Reject
 * only when the status is `unverified`. Each test mutates state then
 * reverses it via API in finally.
 */

test.describe('/vendors verify/reject (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
		await page.goto('/vendors');
		await page.waitForLoadState('networkidle');
	});

	test('row actions cell shows Verify + Reject only for unverified', async ({
		page
	}) => {
		const unverified = await findVendorByStatus(page, 'unverified');
		expect(unverified).toBeTruthy();
		const active = await findVendorByStatus(page, 'active');
		expect(active).toBeTruthy();

		const unverifiedRow = page.locator('table tbody tr', {
			hasText: unverified!.name
		});
		await expect(unverifiedRow.getByRole('button', { name: 'Verify' })).toBeVisible();
		await expect(unverifiedRow.getByRole('button', { name: 'Reject' })).toBeVisible();

		const activeRow = page.locator('table tbody tr', { hasText: active!.name });
		await expect(activeRow.getByRole('button', { name: 'Verify' })).toHaveCount(0);
		await expect(activeRow.getByRole('button', { name: 'Reject' })).toHaveCount(0);
	});

	test('Verify flips an unverified vendor to active', async ({ page }) => {
		const target = await findVendorByStatus(page, 'unverified');
		expect(target).toBeTruthy();

		try {
			const row = page.locator('table tbody tr', { hasText: target!.name });
			const verified = page.waitForResponse(
				(r) =>
					r.url().includes(`/api/vendors/${target!.id}/verify`) &&
					r.request().method() === 'POST'
			);
			await row.getByRole('button', { name: 'Verify' }).click();
			const resp = await verified;
			expect(resp.status()).toBe(200);

			// After fetchVendors, the row's actions cell loses the buttons.
			await expect(
				row.getByRole('button', { name: 'Verify' })
			).toHaveCount(0, { timeout: 5_000 });
		} finally {
			await setVendorStatus(page, target!.id, 'unverified');
		}
	});

	test('Reject flips an unverified vendor to rejected', async ({ page }) => {
		const target = await findVendorByStatus(page, 'unverified');
		expect(target).toBeTruthy();

		try {
			const row = page.locator('table tbody tr', { hasText: target!.name });
			const rejected = page.waitForResponse(
				(r) =>
					r.url().includes(`/api/vendors/${target!.id}/reject`) &&
					r.request().method() === 'POST'
			);
			await row.getByRole('button', { name: 'Reject' }).click();
			const resp = await rejected;
			expect(resp.status()).toBe(200);

			// Row gets the .rejected class once status flips.
			await expect(row).toHaveClass(/rejected/, { timeout: 5_000 });
		} finally {
			await setVendorStatus(page, target!.id, 'unverified');
		}
	});
});
