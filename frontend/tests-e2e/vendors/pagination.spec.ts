import { API_BASE, authedTenantHeaders, expect, tenantPsql, test } from '../fixtures/helpers';

/**
 * /vendors pagination. Bulk-insert vendors past the page_size=20 boundary and
 * assert the shared Load-More contract plus the API default page size.
 */

const MARKER = 'PAGE-VND-';

function seedVendors(n: number): void {
	tenantPsql(
		`INSERT INTO vendors (id, organization_id, name, status, source, accepts_virtual_cards, is_1099_eligible, kyc_status, created_at, updated_at)
		 SELECT gen_random_uuid(), (SELECT organization_id FROM vendors LIMIT 1),
		        '${MARKER}' || lpad(g::text, 3, '0'), 'active', 'manual', false, false, 'not_required', now(), now()
		 FROM generate_series(1, ${n}) g`
	);
}

function purge(): void {
	tenantPsql(`DELETE FROM vendors WHERE name LIKE '${MARKER}%'`);
}

test.describe('/vendors pagination', () => {
	test.afterEach(() => purge());

	test('Load more appends the next page', async ({ page }) => {
		seedVendors(22);

		await page.goto('/vendors');
		await page.waitForLoadState('networkidle');

		const firstPageRows = await page.locator('table tbody tr').count();
		expect(firstPageRows).toBeLessThanOrEqual(20);

		const loadMore = page.getByRole('button', { name: /Load more/ });
		await expect(loadMore).toBeVisible();
		const total = Number((await loadMore.textContent())?.match(/of\s+(\d+)/)?.[1]);
		expect(total).toBeGreaterThanOrEqual(22);

		const next = page.waitForResponse(
			(r) => r.url().includes('/api/vendors') && r.url().includes('page=2')
		);
		await loadMore.click();
		await next;
		expect(await page.locator('table tbody tr').count()).toBeGreaterThan(firstPageRows);
	});

	test('API default page size is 20', async ({ page }) => {
		seedVendors(25);
		const resp = await page.request.get(`${API_BASE}/api/vendors`, {
			headers: await authedTenantHeaders(page)
		});
		const body = (await resp.json()) as { items: unknown[]; total: number; page_size: number };
		expect(body.page_size).toBe(20);
		expect(body.items.length).toBeLessThanOrEqual(20);
		expect(body.total).toBeGreaterThanOrEqual(25);
	});
});
