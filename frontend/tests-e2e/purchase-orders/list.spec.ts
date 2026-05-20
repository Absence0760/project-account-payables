import { API_BASE, authedTenantHeaders, expect, test } from '../fixtures/helpers';

/**
 * /purchase-orders — list page + detail modal.
 *
 * The seed creates 5 POs per tenant via scripts/seed.py. The list
 * endpoint paginates at 20/page (well above seed count) so the table
 * renders all of them on the first page.
 */

test.describe('/purchase-orders', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/purchase-orders');
		await page.waitForLoadState('networkidle');
	});

	test('renders the seeded POs', async ({ page }) => {
		await expect(page.getByRole('heading', { name: 'Purchase Orders' })).toBeVisible();
		const rows = page.locator('table tbody tr');
		await expect(rows.first()).toBeVisible({ timeout: 5_000 });
		expect(await rows.count()).toBeGreaterThan(0);
	});

	test('search input filters the visible PO list', async ({ page }) => {
		const before = await page.locator('table tbody tr').count();
		// Pick the first row's PO number, search for a substring.
		const firstPoNumber = await page.locator('table tbody tr td.mono').first().textContent();
		expect(firstPoNumber).toBeTruthy();
		const stem = firstPoNumber!.trim().slice(0, -1); // drop the last char to keep it a substring

		const filtered = page.waitForResponse(
			(r) => r.url().includes('/api/purchase-orders') && r.url().includes('search=')
		);
		await page.getByPlaceholder('Search PO number...').fill(stem);
		await filtered;

		const after = await page.locator('table tbody tr').count();
		expect(after).toBeGreaterThan(0);
		expect(after).toBeLessThanOrEqual(before);
	});

	test('clicking a row opens the detail modal with line items', async ({ page }) => {
		await page.locator('table tbody tr').first().click();

		const modal = page.locator('div.modal[role="dialog"][aria-label="Purchase order"]');
		await expect(modal).toBeVisible({ timeout: 5_000 });
		await expect(modal.locator('h2')).toHaveText('Purchase Order');
		await expect(modal.getByRole('heading', { name: 'Line Items' })).toBeVisible();
		// At least one line item row in the line-items table.
		const lineRows = modal.locator('.line-table').first().locator('tbody tr');
		expect(await lineRows.count()).toBeGreaterThan(0);

		// Linked invoices section is rendered (count may be 0).
		await expect(modal.getByRole('heading', { name: /Linked Invoices/ })).toBeVisible();

		// Close.
		await modal.getByRole('button', { name: 'Close' }).click();
		await expect(modal).toBeHidden();
	});

	test('GET /api/purchase-orders/{id} returns the matching PO with linked invoices', async ({
		page
	}) => {
		const headers = await authedTenantHeaders(page);
		const list = await page.request.get(`${API_BASE}/api/purchase-orders`, { headers });
		const listBody = (await list.json()) as { items: Array<{ id: string; po_number: string }> };
		expect(listBody.items.length).toBeGreaterThan(0);

		const target = listBody.items[0];
		const detail = await page.request.get(`${API_BASE}/api/purchase-orders/${target.id}`, {
			headers
		});
		expect(detail.status()).toBe(200);
		const body = (await detail.json()) as {
			id: string;
			po_number: string;
			line_items: unknown[];
			linked_invoices: unknown[];
		};
		expect(body.id).toBe(target.id);
		expect(body.po_number).toBe(target.po_number);
		expect(Array.isArray(body.line_items)).toBe(true);
		expect(Array.isArray(body.linked_invoices)).toBe(true);
	});

	test('GET /api/purchase-orders/{id} returns 404 for an unknown id', async ({ page }) => {
		const resp = await page.request.get(
			`${API_BASE}/api/purchase-orders/00000000-0000-0000-0000-000000000000`,
			{ headers: await authedTenantHeaders(page) }
		);
		expect(resp.status()).toBe(404);
	});

	test('list endpoint returns paginated {items, total} shape', async ({ page }) => {
		const resp = await page.request.get(`${API_BASE}/api/purchase-orders?page_size=2`, {
			headers: await authedTenantHeaders(page)
		});
		expect(resp.status()).toBe(200);
		const body = (await resp.json()) as { items: unknown[]; total: number };
		expect(Array.isArray(body.items)).toBe(true);
		expect(body.items.length).toBeLessThanOrEqual(2);
		expect(typeof body.total).toBe('number');
		expect(body.total).toBeGreaterThanOrEqual(body.items.length);
	});
});
