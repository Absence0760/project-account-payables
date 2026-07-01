import {
	API_BASE,
	authedTenantHeaders,
	expect,
	signInAndWait,
	test
} from '../fixtures/helpers';

/**
 * /positive-pay — Positive Pay / Payment Fraud File.
 *
 * Exercises the Positive Pay surface against the same contract the backend
 * `/api/positive-pay` router + the `/positive-pay` frontend route are built to:
 * render the list page (header, KPI row, filter chips, table), open the
 * generate modal, and confirm the RBAC posture (read = admin/ap_manager/cfo,
 * write = admin/ap_manager; clerks excluded entirely — a treasury control).
 *
 * Login model mirrors the rest of the suite: the default per-worker storage
 * state signs the worker's admin in (an admin is in both the read and the
 * mutate set), so the page loads directly without a redirect. The clerk
 * describe block opts out and signs in as the clerk.
 *
 * Robust without a pre-seeded executed check run: every assertion targets the
 * rendered page, the modal, or a direct API RBAC check — no seeded Positive Pay
 * file is required.
 */

test.describe('/positive-pay (admin)', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/positive-pay');
		await page.waitForLoadState('networkidle');
	});

	test('renders the Positive Pay surface — header, KPIs, filters, table', async ({ page }) => {
		await expect(page.getByRole('heading', { name: 'Positive Pay' })).toBeVisible();

		// KPI row.
		await expect(page.locator('.kpi').first()).toBeVisible({ timeout: 10_000 });

		// File-type filter chips.
		await expect(page.locator('.filter-chip', { hasText: 'All' })).toBeVisible();
		await expect(page.locator('.filter-chip', { hasText: 'Check issue' })).toBeVisible();
		await expect(page.locator('.filter-chip', { hasText: 'ACH auth' })).toBeVisible();

		// The files table renders (seeded rows or the centred empty state).
		await expect(page.locator('.grid-container table')).toBeVisible();
	});

	test('switching the file-type filter re-requests the list', async ({ page }) => {
		const respPromise = page.waitForResponse(
			(r) => r.url().includes('/api/positive-pay') && r.url().includes('file_type=ach_authorization')
		);
		await page.locator('.filter-chip', { hasText: 'ACH auth' }).click();
		const resp = await respPromise;
		expect(resp.request().url()).toContain('file_type=ach_authorization');
	});

	test('the generate modal opens with the expected aria-label', async ({ page }) => {
		await page.getByRole('button', { name: '+ Generate file' }).click();
		const dialog = page.getByRole('dialog', { name: 'Generate positive pay file' });
		await expect(dialog).toBeVisible();
		// File-type + bank-format controls are present.
		await expect(dialog.getByLabel('File type')).toBeVisible();
		await expect(dialog.getByLabel('Bank format')).toBeVisible();
	});

	test('the file total renders in the stored per-file currency, not a hardcoded USD', async ({
		page
	}) => {
		// Each file now carries its OWN currency (the org reporting currency
		// stamped at generation). Patch the list response to a non-USD currency
		// and assert the total cell follows it — a hardcoded USD fallback would
		// show "$". Fresh page per test → no leak into the USD-default tests.
		await page.route('**/api/positive-pay?**', async (route) => {
			if (route.request().method() !== 'GET') return route.continue();
			const resp = await route.fetch();
			const body = await resp.json();
			body.items = (body.items ?? []).map((f: Record<string, unknown>) => ({
				...f,
				currency: 'EUR'
			}));
			await route.fulfill({ response: resp, json: body });
		});

		let id: string | null = null;
		try {
			// page.request bypasses page.route, so this create hits the real API.
			const resp = await page.request.post(`${API_BASE}/api/positive-pay/ach-authorization`, {
				headers: await authedTenantHeaders(page),
				data: { bank_format: 'csv' }
			});
			expect(resp.ok()).toBeTruthy();
			id = ((await resp.json()) as { id: string }).id;

			await page.goto('/positive-pay?file_type=ach_authorization');
			await page.waitForLoadState('networkidle');

			const row = page.locator('tr', { hasText: id!.slice(0, 8) });
			await expect(row).toBeVisible({ timeout: 10_000 });
			// Total cell (file, type, format, items, TOTAL) — 5th column, index 4.
			const totalCell = row.locator('td').nth(4);
			await expect(totalCell).toContainText('€');
			await expect(totalCell).not.toContainText('$');
		} finally {
			if (id) {
				await page.request.delete(`${API_BASE}/api/positive-pay/${id}`, {
					headers: await authedTenantHeaders(page)
				});
			}
		}
	});

	test('a generated ACH file stamps + returns the org reporting currency (USD default)', async ({
		page
	}) => {
		// No reporting currency configured → the file falls back to the platform
		// default (USD) and returns it, proving the column is populated at
		// creation (not left null for fresh files).
		let id: string | null = null;
		try {
			const resp = await page.request.post(`${API_BASE}/api/positive-pay/ach-authorization`, {
				headers: await authedTenantHeaders(page),
				data: { bank_format: 'csv' }
			});
			expect(resp.ok()).toBeTruthy();
			const file = (await resp.json()) as { id: string; currency: string };
			id = file.id;
			expect(file.currency).toBe('USD');
		} finally {
			if (id) {
				await page.request.delete(`${API_BASE}/api/positive-pay/${id}`, {
					headers: await authedTenantHeaders(page)
				});
			}
		}
	});

	test('generating an ACH authorization file lands it in the list', async ({ page }) => {
		// ACH authorization needs no payment run, so it generates from any tenant
		// state. Drive it via the API (the modal path is covered above) then assert
		// the row renders, and clean up.
		let id: string | null = null;
		try {
			const resp = await page.request.post(`${API_BASE}/api/positive-pay/ach-authorization`, {
				headers: await authedTenantHeaders(page),
				data: { bank_format: 'csv' }
			});
			expect(resp.ok()).toBeTruthy();
			const file = (await resp.json()) as { id: string; file_type: string };
			id = file.id;
			expect(file.file_type).toBe('ach_authorization');

			await page.goto('/positive-pay?file_type=ach_authorization');
			await page.waitForLoadState('networkidle');
			await expect(
				page.getByRole('button', { name: new RegExp(`Open Positive Pay file.*${id!.slice(0, 8)}`) })
			).toBeVisible({ timeout: 10_000 });
		} finally {
			if (id) {
				await page.request.delete(`${API_BASE}/api/positive-pay/${id}`, {
					headers: await authedTenantHeaders(page)
				});
			}
		}
	});
});

test.describe('/positive-pay (clerk — no access)', () => {
	// Opt out of the default admin storage state so we can sign in as the clerk.
	test.use({ storageState: { cookies: [], origins: [] } });

	test('ap_clerk is excluded from Positive Pay (no nav, write API 403s)', async ({
		page,
		tenantClerk
	}) => {
		await signInAndWait(page, tenantClerk);

		// Positive Pay is gated to admin/ap_manager/cfo — the clerk never sees the
		// nav entry.
		await page.goto('/');
		await page.waitForLoadState('networkidle');
		await expect(page.getByRole('link', { name: 'Positive Pay' })).toHaveCount(0);

		// And a mutate call is rejected by the backend (admin / ap_manager only).
		// `require_roles` runs before the handler body, so the POST 403s regardless
		// of payload.
		const resp = await page.request.post(`${API_BASE}/api/positive-pay/ach-authorization`, {
			headers: await authedTenantHeaders(page),
			data: { bank_format: 'csv' }
		});
		expect(resp.status()).toBe(403);
	});
});
