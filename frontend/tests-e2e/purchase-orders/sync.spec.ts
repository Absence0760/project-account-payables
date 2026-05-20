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
 * /api/purchase-orders/sync-erp — adapter-driven sync.
 *
 * Roadmap item "PO sync from ERP — needs real ERP-adapter `list_pos()`"
 * (2/3-Way PO Matching section).
 *
 * The endpoint dispatches through `get_erp_adapter()` instead of the
 * old hardcoded mock list inside the route. The mock adapter returns a
 * deterministic three-PO catalogue, so this spec asserts:
 *   1. With ERP unconfigured, sync returns 400 (the explicit precondition).
 *   2. With the mock ERP configured, sync inserts the three POs once,
 *      then a second sync inserts 0 (the existence check fires).
 *   3. The synced POs show up on GET /api/purchase-orders so the UI
 *      can render them — this is the integration boundary that broke
 *      most often during refactors.
 */

const MOCK_PO_NUMBERS = ['PO-2024-200', 'PO-2024-201', 'PO-2024-202'];

test.describe('/api/purchase-orders/sync-erp', () => {
	// Other suites (e.g. payments/execute) assume the seeded org has NO
	// ERP configured — `dispatch_payment_sync` only runs the invoice →
	// paid auto-bump when erp_config is present. Leaving the mock ERP
	// wired up after this suite finishes makes the payments execute test
	// see `paid` instead of `payment_scheduled`.
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
		const headers = await apiHeaders(page);
		// Wipe any prior ERP config from a previous run.
		await page.request.patch(`${API_BASE}/api/organization`, {
			headers: { ...headers, 'Content-Type': 'application/json' },
			data: { settings: { erp: null } }
		});

		const resp = await page.request.post(`${API_BASE}/api/purchase-orders/sync-erp`, {
			headers
		});
		expect(resp.status()).toBe(400);
		const body = (await resp.json()) as { detail: string };
		expect(body.detail).toMatch(/no erp/i);
	});

	test('syncs the mock-adapter PO catalogue, idempotent on re-run', async ({ page }) => {
		const headers = await apiHeaders(page);

		// 1. Wire the mock ERP into the org settings.
		await page.request.patch(`${API_BASE}/api/organization`, {
			headers: { ...headers, 'Content-Type': 'application/json' },
			data: { settings: { erp: { type: 'mock', integration_method: 'direct' } } }
		});

		// 2. First sync — every PO already in the DB from a prior run is
		//    "skipped"; the rest are "created". Either way, after this call
		//    runs, all three mock POs are in the DB.
		const first = await page.request.post(`${API_BASE}/api/purchase-orders/sync-erp`, {
			headers
		});
		expect(first.status()).toBe(200);
		const firstBody = (await first.json()) as {
			success: boolean;
			created: number;
			skipped: number;
			adapter: string;
			message: string;
		};
		expect(firstBody.success).toBe(true);
		expect(firstBody.adapter).toBe('mock');
		expect(firstBody.created + firstBody.skipped).toBe(MOCK_PO_NUMBERS.length);

		// 3. Second sync — every PO now exists, so created=0 / skipped=3.
		//    This is the dedupe guarantee the sync flow relies on; without
		//    it, every click would multiply the catalogue.
		const second = await page.request.post(`${API_BASE}/api/purchase-orders/sync-erp`, {
			headers
		});
		expect(second.status()).toBe(200);
		const secondBody = (await second.json()) as {
			created: number;
			skipped: number;
		};
		expect(secondBody.created).toBe(0);
		expect(secondBody.skipped).toBe(MOCK_PO_NUMBERS.length);

		// 4. The synced POs are listable by the regular endpoint.
		const list = await page.request.get(`${API_BASE}/api/purchase-orders?page_size=200`, {
			headers
		});
		expect(list.status()).toBe(200);
		const listBody = (await list.json()) as {
			items: Array<{ po_number: string; total: number; line_items: unknown[] }>;
		};
		const numbers = listBody.items.map((po) => po.po_number);
		for (const expected of MOCK_PO_NUMBERS) {
			expect(numbers).toContain(expected);
		}

		// 5. Spot-check that line items and totals were persisted (not
		//    just the PO header). PO-2024-200 has three lines @ 2500 total.
		const target = listBody.items.find((po) => po.po_number === 'PO-2024-200')!;
		expect(target.total).toBeCloseTo(2500.0, 2);
		expect(target.line_items.length).toBe(3);
	});

	test('clerk role cannot trigger the sync', async ({ page, tenantClerk }) => {
		// Sign in as the clerk (read-only on POs). Sync requires admin or
		// ap_manager — this is the RBAC contract.
		await signInAndWait(page, tenantClerk);
		const headers = await apiHeaders(page);

		const resp = await page.request.post(`${API_BASE}/api/purchase-orders/sync-erp`, {
			headers
		});
		expect(resp.status()).toBe(403);
	});
});
