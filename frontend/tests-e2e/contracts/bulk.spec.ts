import { API_BASE, authedTenantHeaders, expect, tenantPsql, test } from '../fixtures/helpers';

/**
 * /contracts bulk operations (issue #328, power-user/Medium): /vendors and
 * /contracts shipped zero bulk actions despite being primary volume list
 * pages. This covers the UI wiring end-to-end — checkbox selection, the
 * shared BulkBar appearing, and the lifecycle bulk action landing —
 * complementing the exhaustive skip-and-report coverage in
 * backend/tests/test_contract_bulk_ops.py.
 */

interface Vendor {
	id: string;
	name: string;
}

async function getFirstVendor(page: import('@playwright/test').Page): Promise<Vendor> {
	const resp = await page.request.get(`${API_BASE}/api/vendors`, {
		headers: await authedTenantHeaders(page)
	});
	const body = (await resp.json()) as { items: Vendor[] };
	return body.items[0];
}

const MARKER = 'BULKCTR-';

async function createContract(
	page: import('@playwright/test').Page,
	vendorId: string,
	number: string
): Promise<string> {
	const resp = await page.request.post(`${API_BASE}/api/contracts`, {
		headers: await authedTenantHeaders(page),
		data: {
			contract_number: number,
			contract_type: 'msa',
			vendor_id: vendorId,
			currency: 'USD',
			total_value: '10000.00'
		}
	});
	expect(resp.status()).toBe(201);
	return ((await resp.json()) as { id: string }).id;
}

function purge(): void {
	tenantPsql(
		`DELETE FROM contract_line_items WHERE contract_id IN (SELECT id FROM contracts WHERE contract_number LIKE '${MARKER}%')`
	);
	tenantPsql(`DELETE FROM contracts WHERE contract_number LIKE '${MARKER}%'`);
}

test.describe('/contracts bulk operations (acme admin)', () => {
	test.afterEach(() => purge());

	test('bulk activate transitions selected draft contracts', async ({ page }) => {
		const vendor = await getFirstVendor(page);
		await createContract(page, vendor.id, `${MARKER}1`);
		await createContract(page, vendor.id, `${MARKER}2`);

		await page.goto('/contracts');
		await page.getByPlaceholder('Search contracts...').fill(MARKER);
		await page.waitForResponse(
			(r) => r.url().includes('/api/contracts?') && r.url().includes('search=')
		);
		await expect(page.locator('table tbody tr')).toHaveCount(2);

		await page.locator('th.checkbox-col input[type="checkbox"]').check();
		await expect(page.locator('.bulk-count')).toContainText('2 selected');

		// Default select value is "Activate" (the first lifecycle option).
		const statusResponse = page.waitForResponse(
			(r) => r.url().includes('/api/contracts/bulk/status') && r.request().method() === 'POST'
		);
		await page.getByRole('button', { name: 'Change Status' }).click();
		const body = (await (await statusResponse).json()) as {
			updated: number;
			skipped: unknown[];
		};
		expect(body.updated).toBe(2);
		expect(body.skipped).toHaveLength(0);

		await expect(page.locator('.bulk-count')).toHaveCount(0);
		const badge = page.locator('table tbody tr').first().locator('td', { hasText: 'Active' });
		await expect(badge).toBeVisible();
	});

	test('bulk export downloads a CSV of the selected contracts', async ({ page }) => {
		const vendor = await getFirstVendor(page);
		await createContract(page, vendor.id, `${MARKER}3`);

		await page.goto('/contracts');
		await page.getByPlaceholder('Search contracts...').fill(MARKER);
		await page.waitForResponse(
			(r) => r.url().includes('/api/contracts?') && r.url().includes('search=')
		);
		await expect(page.locator('table tbody tr')).toHaveCount(1);

		await page.locator('th.checkbox-col input[type="checkbox"]').check();
		await expect(page.locator('.bulk-count')).toContainText('1 selected');

		const [download] = await Promise.all([
			page.waitForEvent('download'),
			page.getByRole('button', { name: 'CSV', exact: true }).click()
		]);
		expect(download.suggestedFilename()).toBe('contracts-export.csv');
	});
});
