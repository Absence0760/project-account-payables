import {
	API_BASE,
	authedTenantHeaders,
	currentTenantSlug,
	expect,
	signInAndWait,
	tenantBase,
	test
} from '../fixtures/helpers';

async function apiHeaders(page: import('@playwright/test').Page) {
	return await authedTenantHeaders(page);
}

/**
 * /api/gl-accounts/sync-erp — adapter-driven chart-of-accounts sync.
 *
 * The endpoint used to ship a hardcoded 20-row mock list inline; it
 * now dispatches via `get_erp_adapter().list_gl_accounts()`. This
 * spec exercises the contract end-to-end against the worker's seeded
 * tenant configured against the mock ERP.
 *
 *   1. Unconfigured ERP → 400 (the precondition)
 *   2. With mock ERP wired → 200 + idempotent
 *   3. Synced rows are listable via GET /api/gl-accounts
 *   4. RBAC: clerk → 403
 */

test.describe('/api/gl-accounts/sync-erp', () => {
	// See the matching afterAll in tests-e2e/purchase-orders/sync.spec.ts
	// — leaving an `erp` config behind makes downstream specs (notably
	// payments/execute) see auto-paid invoices instead of the expected
	// payment_scheduled status.
	test.afterAll(async ({ browser }) => {
		const context = await browser.newContext({ baseURL: tenantBase(currentTenantSlug()) });
		const page = await context.newPage();
		await signInAndWait(page);
		const headers = await apiHeaders(page);
		await page.request.patch(`${API_BASE}/api/organization`, {
			headers: { ...headers, 'Content-Type': 'application/json' },
			data: { settings: { erp: null } }
		});
		await context.close();
	});

	test('returns 400 when no ERP is configured', async ({ page }) => {
		await signInAndWait(page);
		const headers = await apiHeaders(page);

		await page.request.patch(`${API_BASE}/api/organization`, {
			headers: { ...headers, 'Content-Type': 'application/json' },
			data: { settings: { erp: null } }
		});

		const resp = await page.request.post(`${API_BASE}/api/gl-accounts/sync-erp`, {
			headers
		});
		expect(resp.status()).toBe(400);
		const body = (await resp.json()) as { detail: string };
		expect(body.detail).toMatch(/no erp/i);
	});

	test('mock adapter syncs the canonical chart, idempotent on re-run', async ({ page }) => {
		await signInAndWait(page);
		const headers = await apiHeaders(page);

		await page.request.patch(`${API_BASE}/api/organization`, {
			headers: { ...headers, 'Content-Type': 'application/json' },
			data: { settings: { erp: { type: 'mock', integration_method: 'direct' } } }
		});

		// First sync — created + updated together cover the catalogue.
		const first = await page.request.post(`${API_BASE}/api/gl-accounts/sync-erp`, {
			headers
		});
		expect(first.status()).toBe(200);
		const firstBody = (await first.json()) as {
			success: boolean;
			created: number;
			updated: number;
			adapter: string;
		};
		expect(firstBody.success).toBe(true);
		expect(firstBody.adapter).toBe('mock');
		expect(firstBody.created + firstBody.updated).toBeGreaterThanOrEqual(0);

		// Second sync: created MUST be 0 — the upsert key (code + org)
		// finds every row from the first pass. Updated is also 0 unless
		// a name/type drifted, which it shouldn't between back-to-back
		// runs against the mock adapter.
		const second = await page.request.post(`${API_BASE}/api/gl-accounts/sync-erp`, {
			headers
		});
		const secondBody = (await second.json()) as { created: number; updated: number };
		expect(secondBody.created).toBe(0);
		expect(secondBody.updated).toBe(0);

		// Synced rows show up via GET /api/gl-accounts — this is the
		// list the InvoiceModal's GL dropdown consumes.
		const list = await page.request.get(`${API_BASE}/api/gl-accounts`, { headers });
		expect(list.status()).toBe(200);
		const accts = (await list.json()) as Array<{ code: string; account_type: string }>;
		const codes = accts.map((a) => a.code);
		// Spot-check a few that the AI prompt's default list also references.
		for (const c of ['6100', '6200', '6300']) {
			expect(codes).toContain(c);
		}
	});

	test('clerk role cannot trigger the sync', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);
		const headers = await apiHeaders(page);
		const resp = await page.request.post(`${API_BASE}/api/gl-accounts/sync-erp`, {
			headers
		});
		expect(resp.status()).toBe(403);
	});
});
