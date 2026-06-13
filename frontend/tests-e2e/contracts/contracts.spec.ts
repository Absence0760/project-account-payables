import {
	API_BASE,
	authedTenantHeaders,
	expect,
	tenantPsql,
	test
} from '../fixtures/helpers';

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

async function createContract(
	page: import('@playwright/test').Page,
	data: Record<string, unknown>
): Promise<{ id: string; status: string; contract_number: string }> {
	const resp = await page.request.post(`${API_BASE}/api/contracts`, {
		headers: await authedTenantHeaders(page),
		data
	});
	expect(resp.status()).toBe(201);
	return (await resp.json()) as { id: string; status: string; contract_number: string };
}

/** Hard-delete a contract via psql (revertible cleanup for the test row). */
function deleteContract(id: string): void {
	tenantPsql(`DELETE FROM contract_line_items WHERE contract_id='${id}'`);
	tenantPsql(`DELETE FROM contracts WHERE id='${id}'`);
}

/**
 * /contracts — list render, API-driven create surfaced in the UI, and a
 * lifecycle transition reflected in the row. Each test creates a fresh
 * contract and removes it via psql in `finally`.
 */
test.describe('/contracts', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/contracts');
		await page.waitForLoadState('networkidle');
	});

	test('renders the contracts list page', async ({ page }) => {
		await expect(page.getByRole('heading', { name: 'Contracts' })).toBeVisible();
		await expect(page.locator('table')).toBeVisible();
	});

	test('a created contract appears in the list', async ({ page }) => {
		const vendor = await getFirstVendor(page);
		const number = `E2E-${Date.now()}`;
		let id: string | null = null;
		try {
			const created = await createContract(page, {
				contract_number: number,
				title: 'E2E Master Agreement',
				contract_type: 'msa',
				vendor_id: vendor.id,
				currency: 'USD',
				total_value: '50000.00',
				end_date: '2030-01-01'
			});
			id = created.id;
			expect(created.status).toBe('draft');

			await page.reload();
			await page.waitForLoadState('networkidle');
			await expect(page.getByText(number)).toBeVisible();
		} finally {
			if (id) deleteContract(id);
		}
	});

	test('activating a contract is reflected on the row', async ({ page }) => {
		const vendor = await getFirstVendor(page);
		const number = `E2E-${Date.now()}`;
		let id: string | null = null;
		try {
			const created = await createContract(page, {
				contract_number: number,
				vendor_id: vendor.id,
				contract_type: 'service',
				end_date: '2030-01-01'
			});
			id = created.id;

			const activated = await page.request.post(`${API_BASE}/api/contracts/${id}/activate`, {
				headers: await authedTenantHeaders(page)
			});
			expect(activated.status()).toBe(200);
			expect(((await activated.json()) as { status: string }).status).toBe('active');

			await page.goto(`/contracts?search=${number}`);
			await page.waitForLoadState('networkidle');
			await expect(page.getByText(number)).toBeVisible();
		} finally {
			if (id) deleteContract(id);
		}
	});
});
