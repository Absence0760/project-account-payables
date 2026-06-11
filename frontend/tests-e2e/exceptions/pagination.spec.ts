import { API_BASE, authedTenantHeaders, expect, tenantPsql, test } from '../fixtures/helpers';

/**
 * /exceptions pagination. The exceptions queue previously fetched every row;
 * it now uses the shared Load-More at page_size=20. The page defaults to the
 * "open" status filter, so we seed open exceptions (against a seeded invoice)
 * past the boundary and assert the contract.
 */

const MARKER = 'PAGE-EXC-';

function seedExceptions(n: number): void {
	tenantPsql(
		`INSERT INTO exceptions (id, invoice_id, exception_type, severity, status, organization_id, description, created_at, updated_at)
		 SELECT gen_random_uuid(), i.id, 'duplicate', 'warning', 'open', i.organization_id, '${MARKER}' || g, now(), now()
		 FROM generate_series(1, ${n}) g, (SELECT id, organization_id FROM invoices LIMIT 1) i`
	);
}

function purge(): void {
	tenantPsql(`DELETE FROM exceptions WHERE description LIKE '${MARKER}%'`);
}

test.describe('/exceptions pagination', () => {
	test.afterEach(() => purge());

	test('Load more appends the next page', async ({ page }) => {
		seedExceptions(22);

		await page.goto('/exceptions');
		await page.waitForLoadState('networkidle');

		const firstPageRows = await page.locator('table tbody tr').count();
		expect(firstPageRows).toBeLessThanOrEqual(20);

		const loadMore = page.getByRole('button', { name: /Load more/ });
		await expect(loadMore).toBeVisible();
		const total = Number((await loadMore.textContent())?.match(/of\s+(\d+)/)?.[1]);
		expect(total).toBeGreaterThanOrEqual(22);

		const next = page.waitForResponse(
			(r) => r.url().includes('/api/exceptions') && r.url().includes('page=2')
		);
		await loadMore.click();
		await next;
		expect(await page.locator('table tbody tr').count()).toBeGreaterThan(firstPageRows);
	});

	test('API default page size is 20', async ({ page }) => {
		seedExceptions(25);
		const resp = await page.request.get(`${API_BASE}/api/exceptions?status=open`, {
			headers: await authedTenantHeaders(page)
		});
		const body = (await resp.json()) as { items: unknown[]; total: number; page_size: number };
		expect(body.page_size).toBe(20);
		expect(body.items.length).toBeLessThanOrEqual(20);
		expect(body.total).toBeGreaterThanOrEqual(25);
	});
});
