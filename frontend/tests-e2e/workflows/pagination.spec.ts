import { API_BASE, authedTenantHeaders, expect, tenantPsql, test } from '../fixtures/helpers';

/**
 * /workflows pagination. The workflows list previously returned a bare array;
 * it now returns the {items,total,page,page_size} envelope and the page uses
 * the shared Load-More at page_size=20. Bulk-insert workflow definitions past
 * the boundary and assert the contract.
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

function purge(): void {
	tenantPsql(`DELETE FROM workflow_definitions WHERE name LIKE '${MARKER}%'`);
}

test.describe('/workflows pagination', () => {
	test.afterEach(() => purge());

	test('Load more appends the next page', async ({ page }) => {
		// Hit the page once so the default workflow exists, giving the SQL seed a
		// row to source organization_id from.
		await page.goto('/workflows');
		await page.waitForLoadState('networkidle');
		seedWorkflows(22);

		await page.reload();
		await page.waitForLoadState('networkidle');

		const firstPageRows = await page.locator('table tbody tr').count();
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
		expect(await page.locator('table tbody tr').count()).toBeGreaterThan(firstPageRows);
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
