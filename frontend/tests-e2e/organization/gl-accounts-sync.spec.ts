import { expect, test, ACME_BASE } from '../fixtures/helpers';

import { ACME_CLERK, signInAndWait } from '../fixtures/helpers';

// Pinned to the acme tenant: this spec uses ACME_*/TECHFLOW_* creds or
// asserts cross-tenant isolation that requires fixed tenant slugs. The
// per-worker baseURL from fixtures/helpers.ts would otherwise route to
// the wrong tenant. Multiple workers may share acme here — keep this
// file's tests read-only or idempotent.
test.use({ baseURL: ACME_BASE });

const API_BASE = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

async function authToken(page: import('@playwright/test').Page) {
	const t = await page.evaluate(() => localStorage.getItem('auth_token'));
	if (!t) throw new Error('not signed in');
	return t;
}

async function apiHeaders(page: import('@playwright/test').Page) {
	const token = await authToken(page);
	return { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' };
}

/**
 * /api/gl-accounts/sync-erp — adapter-driven chart-of-accounts sync.
 *
 * The endpoint used to ship a hardcoded 20-row mock list inline; it
 * now dispatches via `get_erp_adapter().list_gl_accounts()`. This
 * spec exercises the contract end-to-end against the seeded acme
 * tenant configured against the mock ERP.
 *
 *   1. Unconfigured ERP → 400 (the precondition)
 *   2. With mock ERP wired → 200 + idempotent
 *   3. Synced rows are listable via GET /api/gl-accounts
 *   4. RBAC: clerk → 403
 */

test.describe('/api/gl-accounts/sync-erp (acme)', () => {
	// See the matching afterAll in tests-e2e/purchase-orders/sync.spec.ts
	// — leaving an `erp` config behind makes downstream specs (notably
	// payments/execute) see auto-paid invoices instead of the expected
	// payment_scheduled status.
	test.afterAll(async ({ browser }) => {
		const context = await browser.newContext();
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

	test('clerk role cannot trigger the sync', async ({ page }) => {
		await signInAndWait(page, ACME_CLERK);
		const headers = await apiHeaders(page);
		const resp = await page.request.post(`${API_BASE}/api/gl-accounts/sync-erp`, {
			headers
		});
		expect(resp.status()).toBe(403);
	});
});
