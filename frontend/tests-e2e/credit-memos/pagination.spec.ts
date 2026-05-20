import {
	API_BASE,
	authedTenantHeaders,
	expect,
	signInAndWait,
	tenantPsql,
	test
} from '../fixtures/helpers';

async function getFirstVendorId(page: import('@playwright/test').Page): Promise<string> {
	const resp = await page.request.get(`${API_BASE}/api/vendors`, {
		headers: await authedTenantHeaders(page)
	});
	const body = (await resp.json()) as { items: Array<{ id: string }> };
	return body.items[0].id;
}

async function createMemo(
	page: import('@playwright/test').Page,
	vendorId: string,
	memoNumber: string
): Promise<string> {
	const resp = await page.request.post(`${API_BASE}/api/credit-memos`, {
		headers: await authedTenantHeaders(page),
		data: { memo_number: memoNumber, vendor_id: vendorId, amount: 10 }
	});
	return ((await resp.json()) as { id: string }).id;
}

function purgeE2EMemos(): void {
	tenantPsql("DELETE FROM credit_memos WHERE memo_number LIKE 'CM-PAGE-%'");
}

/**
 * /credit-memos pagination: backend defaults to page_size=20 with a
 * total count; the UI renders 20 rows then a "Load more (X of TOTAL)"
 * button that appends the next page. Once everything's loaded the
 * button is replaced by "Showing all N credit memos".
 */

test.describe('/credit-memos pagination', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
	});

	test.afterEach(() => {
		purgeE2EMemos();
	});

	test('large memo count surfaces Load more, which appends the next page', async ({ page }) => {
		const vendorId = await getFirstVendorId(page);
		const ts = Date.now();
		const created: string[] = [];
		for (let i = 0; i < 22; i++) {
			created.push(await createMemo(page, vendorId, `CM-PAGE-${ts}-${String(i).padStart(2, '0')}`));
		}
		expect(created).toHaveLength(22);

		await page.goto('/credit-memos');
		await page.waitForLoadState('networkidle');

		// First page loads 20 of the 22 freshly-created memos. Existing
		// seed memos (if any) may also be in the list; the contract we
		// check is "≤20 rows visible, Load more visible, total≥22".
		const firstPageRows = await page.locator('table tbody tr').count();
		expect(firstPageRows).toBeLessThanOrEqual(20);

		const loadMore = page.getByRole('button', { name: /Load more/ });
		await expect(loadMore).toBeVisible();
		const matched = (await loadMore.textContent())?.match(/(\d+)\s+of\s+(\d+)/);
		expect(matched).toBeTruthy();
		const totalReported = Number(matched![2]);
		expect(totalReported).toBeGreaterThanOrEqual(22);

		// Click — appends the next page, count grows.
		const next = page.waitForResponse(
			(r) => r.url().includes('/api/credit-memos') && r.url().includes('page=2')
		);
		await loadMore.click();
		await next;
		const secondPageRows = await page.locator('table tbody tr').count();
		expect(secondPageRows).toBeGreaterThan(firstPageRows);
	});

	test('default page_size on the API is 20', async ({ page }) => {
		const vendorId = await getFirstVendorId(page);
		const ts = Date.now();
		const ids: string[] = [];
		for (let i = 0; i < 25; i++) {
			ids.push(await createMemo(page, vendorId, `CM-PAGE-default-${ts}-${i}`));
		}
		expect(ids).toHaveLength(25);

		const resp = await page.request.get(`${API_BASE}/api/credit-memos`, {
			headers: await authedTenantHeaders(page)
		});
		const body = (await resp.json()) as { items: unknown[]; total: number };
		expect(body.items.length).toBeLessThanOrEqual(20);
		expect(body.total).toBeGreaterThanOrEqual(25);
	});
});
