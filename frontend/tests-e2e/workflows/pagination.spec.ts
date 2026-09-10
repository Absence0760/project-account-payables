import {
	API_BASE,
	authedTenantHeaders,
	deleteWorkflowsWhere,
	expect,
	tenantPsql,
	test
} from '../fixtures/helpers';

/**
 * /workflows pagination. The workflows list previously returned a bare array;
 * it now returns the {items,total,page,page_size} envelope and the page uses
 * the shared Load-More at page_size=20. Bulk-insert workflow definitions past
 * the boundary and assert the contract.
 */

/**
 * The rows this spec seeds are bulk-INSERTed and never handed to the API, so
 * nothing FK-references them today and a bare definition delete would succeed.
 * Teardown still goes through `deleteWorkflowsWhere`, which owns the walk
 * below the definition — a definition that ever acquires a child stops being
 * deletable, and this is the file the next spec's teardown gets copied from —
 * plus the `is_default = false` seatbelt that keeps a marker typo away from
 * the seeded default `fixtures/globalSetup.ts` asserts against.
 */
const MARKER = 'PAGE-WF-';

function seedWorkflows(n: number): void {
	tenantPsql(
		`INSERT INTO workflow_definitions (id, organization_id, name, steps_config, is_active, is_default, created_at, updated_at)
		 SELECT gen_random_uuid(), (SELECT organization_id FROM workflow_definitions LIMIT 1),
		        '${MARKER}' || lpad(g::text, 3, '0'), '{"steps": []}'::jsonb, false, false, now(), now()
		 FROM generate_series(1, ${n}) g`
	);
}

test.describe('/workflows pagination', () => {
	test.afterEach(() => deleteWorkflowsWhere(MARKER));

	test('Load more appends the next page', async ({ page }) => {
		// Hit the list endpoint once so the default workflow exists, giving the
		// SQL seed a row to source organization_id from. `page.request` rather
		// than the page's own fetch: it is the same call the sibling test makes
		// below, and it is AWAITED — waiting for the network to fall quiet was
		// only ever a guess that the provisioning GET had finished.
		await page.request.get(`${API_BASE}/api/workflows`, {
			headers: await authedTenantHeaders(page)
		});
		seedWorkflows(22);

		await page.goto('/workflows');

		// 22 seeded rows plus the tenant's own definitions, against page_size
		// 20, means the first page is a full one. Asserting the exact count
		// auto-waits and cannot be satisfied by DataTable's single loading
		// placeholder <tr>, which the bare `count()` below would otherwise read.
		const rows = page.locator('table tbody tr');
		await expect(rows).toHaveCount(20);
		const firstPageRows = await rows.count();
		expect(firstPageRows).toBeLessThanOrEqual(20);

		const loadMore = page.getByRole('button', { name: /Load more/ });
		await expect(loadMore).toBeVisible();
		const total = Number((await loadMore.textContent())?.match(/of\s+(\d+)/)?.[1]);
		expect(total).toBeGreaterThanOrEqual(22);

		const next = page.waitForResponse(
			(r) => r.url().includes('/api/workflows') && r.url().includes('page=2')
		);
		await loadMore.click();
		await next;
		// The response arriving is not the rows being rendered, so poll rather
		// than read once.
		await expect.poll(() => rows.count()).toBeGreaterThan(firstPageRows);
	});

	test('API returns the paginated envelope with default page size 20', async ({ page }) => {
		await page.request.get(`${API_BASE}/api/workflows`, {
			headers: await authedTenantHeaders(page)
		}); // ensure the default exists
		seedWorkflows(25);
		const resp = await page.request.get(`${API_BASE}/api/workflows`, {
			headers: await authedTenantHeaders(page)
		});
		const body = (await resp.json()) as { items: unknown[]; total: number; page_size: number };
		expect(body.page_size).toBe(20);
		expect(body.items.length).toBeLessThanOrEqual(20);
		expect(body.total).toBeGreaterThanOrEqual(25);
	});
});
