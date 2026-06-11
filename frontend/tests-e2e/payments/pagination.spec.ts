import { API_BASE, authedTenantHeaders, expect, tenantPsql, test } from '../fixtures/helpers';

/**
 * /payments history-tab pagination. The history tab previously fetched
 * page_size=100 and never paginated; it now uses the shared Load-More at
 * page_size=20. Bulk-insert payments (against a seeded invoice) past the
 * boundary and assert the contract. The History tab isn't the default
 * (queue is), so the test switches to it first.
 */

const MARKER = 'PAGE-PAY-';

function seedPayments(n: number): void {
	tenantPsql(
		`INSERT INTO payments (id, invoice_id, amount, method, status, reference, created_at, updated_at)
		 SELECT gen_random_uuid(), (SELECT id FROM invoices LIMIT 1), 100.00, 'ach', 'completed',
		        '${MARKER}' || g, now(), now()
		 FROM generate_series(1, ${n}) g`
	);
}

function purge(): void {
	tenantPsql(`DELETE FROM payments WHERE reference LIKE '${MARKER}%'`);
}

test.describe('/payments pagination', () => {
	test.afterEach(() => purge());

	test('history tab Load more appends the next page', async ({ page }) => {
		seedPayments(22);

		await page.goto('/payments');
		await page.waitForLoadState('networkidle');
		await page.getByRole('button', { name: /History/ }).click();
		await page.waitForResponse((r) => r.url().includes('/api/payments') && r.url().includes('page=1'));

		const firstPageRows = await page.locator('table tbody tr').count();
		expect(firstPageRows).toBeLessThanOrEqual(20);

		const loadMore = page.getByRole('button', { name: /Load more/ });
		await expect(loadMore).toBeVisible();
		const total = Number((await loadMore.textContent())?.match(/of\s+(\d+)/)?.[1]);
		expect(total).toBeGreaterThanOrEqual(22);

		const next = page.waitForResponse(
			(r) => r.url().includes('/api/payments') && r.url().includes('page=2')
		);
		await loadMore.click();
		await next;
		expect(await page.locator('table tbody tr').count()).toBeGreaterThan(firstPageRows);
	});

	test('API default page size is 20', async ({ page }) => {
		seedPayments(25);
		const resp = await page.request.get(`${API_BASE}/api/payments`, {
			headers: await authedTenantHeaders(page)
		});
		const body = (await resp.json()) as { items: unknown[]; total: number; page_size: number };
		expect(body.page_size).toBe(20);
		expect(body.items.length).toBeLessThanOrEqual(20);
		expect(body.total).toBeGreaterThanOrEqual(25);
	});
});
