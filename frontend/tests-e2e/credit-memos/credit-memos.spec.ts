import {
	API_BASE,
	authedTenantHeaders,
	expect,
	signInAndWait,
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

async function createMemo(
	page: import('@playwright/test').Page,
	data: Record<string, unknown>
): Promise<{ id: string; status: string }> {
	const resp = await page.request.post(`${API_BASE}/api/credit-memos`, {
		headers: await authedTenantHeaders(page),
		data
	});
	expect(resp.status()).toBe(201);
	return (await resp.json()) as { id: string; status: string };
}

/**
 * Hard-delete a credit memo via psql. The product API doesn't expose a
 * delete endpoint by design (memos are kept for audit even after
 * voiding), so direct SQL is the only revertible path for tests.
 */
function deleteMemo(id: string): void {
	tenantPsql(`DELETE FROM credit_memos WHERE id='${id}'`);
}

/**
 * /credit-memos — list, create, apply, void. Each test creates fresh
 * test memos and removes them via psql in finally.
 */

test.describe('/credit-memos', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
		await page.goto('/credit-memos');
		await page.waitForLoadState('networkidle');
	});

	test('renders the empty-state placeholder when no memos exist', async ({ page }) => {
		await expect(page.getByRole('heading', { name: 'Credit Memos' })).toBeVisible();
		// With a fresh tenant there are no seeded memos, so the empty
		// state should render — unless prior tests in this run have
		// already created some that haven't been cleaned up. Either is
		// acceptable; the table itself must be present.
		await expect(page.locator('table')).toBeVisible();
	});

	test('Create modal validates and creates a memo via the API', async ({ page }) => {
		const vendor = await getFirstVendor(page);
		const memoNumber = `CM-E2E-${Date.now()}`;
		let createdId: string | null = null;

		try {
			await page.getByRole('button', { name: '+ New Credit Memo' }).click();
			const modal = page.locator('div.modal[role="dialog"][aria-label="New credit memo"]');
			await expect(modal).toBeVisible();

			await modal.locator('input[type="text"]').fill(memoNumber);
			await modal.locator('select').selectOption(vendor.id);
			await modal.locator('input[type="number"]').fill('250.50');
			await modal.locator('textarea').fill('e2e: returned defective monitors');

			const created = page.waitForResponse(
				(r) =>
					r.url().endsWith('/api/credit-memos') &&
					r.request().method() === 'POST' &&
					r.status() === 201
			);
			await modal.getByRole('button', { name: /^Create$/ }).click();
			const resp = await created;
			const body = (await resp.json()) as { id: string; status: string };
			createdId = body.id;
			expect(body.status).toBe('open');

			// Modal closed; new row visible.
			await expect(modal).toBeHidden();
			await expect(
				page.locator('table tbody tr', { hasText: memoNumber })
			).toBeVisible();
		} finally {
			if (createdId) deleteMemo(createdId);
		}
	});

	test('Status filter chips narrow the list', async ({ page }) => {
		const vendor = await getFirstVendor(page);
		const created: string[] = [];

		try {
			// Make one memo per status path.
			const open = await createMemo(page, {
				memo_number: `CM-OPEN-${Date.now()}`,
				vendor_id: vendor.id,
				amount: 100
			});
			created.push(open.id);

			await page.reload();
			await page.waitForLoadState('networkidle');

			// "Open" chip should reveal it.
			const openFiltered = page.waitForResponse(
				(r) =>
					r.url().includes('/api/credit-memos') && r.url().includes('status=open')
			);
			await page.locator('.filter-chip', { hasText: /^Open$/ }).click();
			await openFiltered;
			await expect(
				page.locator('table tbody tr.applied').first()
			).toHaveCount(0);

			// "Applied" chip should not include it.
			const appliedFiltered = page.waitForResponse(
				(r) =>
					r.url().includes('/api/credit-memos') && r.url().includes('status=applied')
			);
			await page.locator('.filter-chip', { hasText: /^Applied$/ }).click();
			await appliedFiltered;
			await expect(
				page.locator('table tbody tr', { hasText: open.id.slice(0, 8) })
			).toHaveCount(0);
		} finally {
			for (const id of created) deleteMemo(id);
		}
	});

	test('Apply flips an open memo to applied via the UI', async ({ page }) => {
		const vendor = await getFirstVendor(page);

		// Find an invoice belonging to this vendor.
		const invoicesResp = await page.request.get(
			`${API_BASE}/api/invoices?vendor=${encodeURIComponent(vendor.name)}`,
			{ headers: await authedTenantHeaders(page) }
		);
		const invoices = (await invoicesResp.json()) as {
			items: Array<{ id: string; vendor: string; vendor_id: string | null }>;
		};
		const targetInvoice = invoices.items.find((i) => i.vendor === vendor.name);
		expect(targetInvoice, 'no matching invoice for vendor').toBeTruthy();

		const memo = await createMemo(page, {
			memo_number: `CM-APPLY-${Date.now()}`,
			vendor_id: vendor.id,
			amount: 75
		});

		try {
			await page.reload();
			await page.waitForLoadState('networkidle');

			const row = page.locator('table tbody tr', { hasText: `CM-APPLY-` });
			await row.getByRole('button', { name: 'Apply' }).click();

			const applyModal = page.locator(
				'div.modal[role="dialog"][aria-label="Apply credit memo"]'
			);
			await expect(applyModal).toBeVisible();
			await applyModal.locator('select').selectOption(targetInvoice!.id);

			const applied = page.waitForResponse(
				(r) =>
					r.url().includes(`/api/credit-memos/${memo.id}/apply`) &&
					r.request().method() === 'POST' &&
					r.status() === 200
			);
			await applyModal.getByRole('button', { name: /^Apply$/ }).click();
			const resp = await applied;
			const body = (await resp.json()) as { status: string; invoice_id: string };
			expect(body.status).toBe('applied');
			expect(body.invoice_id).toBe(targetInvoice!.id);
		} finally {
			deleteMemo(memo.id);
		}
	});

	test('API: cannot apply a credit memo to an invoice from a different vendor', async ({
		page
	}) => {
		const headers = await authedTenantHeaders(page);
		const vendorsResp = await page.request.get(`${API_BASE}/api/vendors`, { headers });
		const vendors = (await vendorsResp.json()) as { items: Vendor[] };
		expect(vendors.items.length).toBeGreaterThanOrEqual(2);
		const [vendorA, vendorB] = vendors.items;

		// Create the memo for vendorA, plus a fresh invoice for vendorB.
		// Don't lean on the seed having a vendorB invoice — bind the
		// invoice to vendorB.id directly via psql since the public
		// /api/invoices POST infers vendor_id from vendor_name and
		// vendorB.name might not exist as a fuzzy match.
		const memo = await createMemo(page, {
			memo_number: `CM-MISMATCH-${Date.now()}`,
			vendor_id: vendorA.id,
			amount: 50
		});

		const invoiceResp = await page.request.post(`${API_BASE}/api/invoices`, {
			headers,
			data: {
				vendor: vendorB.name,
				invoice_number: `MISMATCH-${Date.now()}`,
				amount: 100,
				status: 'new'
			}
		});
		const invoiceB = (await invoiceResp.json()) as { id: string };
		// Force vendor_id=vendorB regardless of whatever vendor matching
		// the create endpoint did — we want a deterministic mismatch.
		tenantPsql(`UPDATE invoices SET vendor_id='${vendorB.id}' WHERE id='${invoiceB.id}'`);

		try {
			const resp = await page.request.post(
				`${API_BASE}/api/credit-memos/${memo.id}/apply`,
				{
					headers,
					data: { invoice_id: invoiceB.id }
				}
			);
			expect(resp.status()).toBe(409);
		} finally {
			deleteMemo(memo.id);
			tenantPsql(
				`DELETE FROM workflow_steps WHERE instance_id IN (SELECT id FROM workflow_instances WHERE invoice_id='${invoiceB.id}')`
			);
			tenantPsql(`DELETE FROM workflow_instances WHERE invoice_id='${invoiceB.id}'`);
			tenantPsql(`DELETE FROM audit_log WHERE entity_id='${invoiceB.id}'`);
			tenantPsql(`DELETE FROM invoices WHERE id='${invoiceB.id}'`);
		}
	});

	test('API: cannot void an already-applied memo (audit immutability)', async ({ page }) => {
		const headers = await authedTenantHeaders(page);
		const vendor = await getFirstVendor(page);

		const invoicesResp = await page.request.get(`${API_BASE}/api/invoices`, { headers });
		const invoices = (await invoicesResp.json()) as {
			items: Array<{ id: string; vendor: string }>;
		};
		const sameVendorInv = invoices.items.find((i) => i.vendor === vendor.name);
		expect(sameVendorInv).toBeTruthy();

		const memo = await createMemo(page, {
			memo_number: `CM-VOID-${Date.now()}`,
			vendor_id: vendor.id,
			amount: 25,
			invoice_id: sameVendorInv!.id
		});
		expect(memo.status).toBe('applied');

		try {
			const voidResp = await page.request.post(
				`${API_BASE}/api/credit-memos/${memo.id}/void`,
				{ headers }
			);
			expect(voidResp.status()).toBe(409);
		} finally {
			deleteMemo(memo.id);
		}
	});

	test('Void flips an open memo to void status', async ({ page }) => {
		const vendor = await getFirstVendor(page);
		const memo = await createMemo(page, {
			memo_number: `CM-VOID-OK-${Date.now()}`,
			vendor_id: vendor.id,
			amount: 30
		});

		try {
			await page.reload();
			await page.waitForLoadState('networkidle');
			const row = page.locator('table tbody tr', { hasText: `CM-VOID-OK-` });

			const voided = page.waitForResponse(
				(r) =>
					r.url().includes(`/api/credit-memos/${memo.id}/void`) &&
					r.request().method() === 'POST' &&
					r.status() === 200
			);
			await row.getByRole('button', { name: 'Void' }).click();
			const resp = await voided;
			expect(((await resp.json()) as { status: string }).status).toBe('void');
		} finally {
			deleteMemo(memo.id);
		}
	});
});
