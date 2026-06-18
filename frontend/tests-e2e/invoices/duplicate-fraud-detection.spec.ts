import {
	API_BASE,
	authedTenantHeaders,
	expect,
	tenantPsql,
	test
} from '../fixtures/helpers';

/**
 * Invoice duplicate detection — the double-payment fraud control.
 *
 * `services/invoice_warnings.refresh_warnings` flags a `duplicate` warning
 * and raises a `duplicate` exception when another invoice shares the same
 * vendor_name + invoice_number. Paying the same invoice twice (whether an
 * honest re-submission or a deliberate split/replay) is one of the most
 * common AP loss events, so this guard must fire.
 *
 * Note: `refresh_warnings` runs on every invoice MUTATION (PATCH), not on
 * bare create — so the contract is: create the first invoice, create the
 * collision, then a mutation on the collision computes the duplicate flag.
 * These tests prove:
 *
 *   1. The second invoice with the same vendor + number gets a `duplicate`
 *      warning AND a `duplicate` exception that points at it.
 *   2. A distinct invoice number for the same vendor does NOT flag duplicate.
 *
 * Everything is driven through the real /api/invoices + /api/exceptions
 * endpoints; cleanup is direct DB.
 */

interface InvoiceResp {
	id: string;
	invoice_number: string;
	warnings: Array<{ type: string; severity: string; message: string }> | null;
}

interface ExceptionRow {
	id: string;
	invoice_id: string;
	exception_type: string;
	status: string;
}

let H: Record<string, string>;
let SLUG: string;

function slugFromPage(page: import('@playwright/test').Page): string {
	return new URL(page.url()).hostname.split('.')[0];
}

async function createInvoice(
	page: import('@playwright/test').Page,
	vendor: string,
	invoiceNumber: string
): Promise<InvoiceResp> {
	const resp = await page.request.post(`${API_BASE}/api/invoices`, {
		headers: H,
		data: {
			vendor,
			invoice_number: invoiceNumber,
			amount: 1234.56,
			currency: 'USD',
			status: 'new'
		}
	});
	expect(resp.status(), `create invoice ${invoiceNumber}`).toBe(201);
	return (await resp.json()) as InvoiceResp;
}

/** PATCH the invoice with a no-op-ish edit (a description) to trigger
 *  refresh_warnings, then return the recomputed invoice. */
async function touchInvoice(
	page: import('@playwright/test').Page,
	id: string
): Promise<InvoiceResp> {
	const resp = await page.request.patch(`${API_BASE}/api/invoices/${id}`, {
		headers: H,
		data: { notes: `touched ${Date.now()}` }
	});
	expect(resp.status(), 'patch invoice').toBe(200);
	return (await resp.json()) as InvoiceResp;
}

async function duplicateExceptionsFor(
	page: import('@playwright/test').Page,
	invoiceId: string
): Promise<ExceptionRow[]> {
	const resp = await page.request.get(`${API_BASE}/api/exceptions?type=duplicate&page_size=100`, {
		headers: H
	});
	expect(resp.status()).toBe(200);
	const body = (await resp.json()) as { items: ExceptionRow[] };
	return body.items.filter((e) => e.invoice_id === invoiceId);
}

function deleteInvoice(id: string): void {
	try {
		tenantPsql(`DELETE FROM exceptions WHERE invoice_id='${id}'`, SLUG);
		tenantPsql(`DELETE FROM workflow_instances WHERE invoice_id='${id}'`, SLUG);
		tenantPsql(`DELETE FROM invoices WHERE id='${id}'`, SLUG);
	} catch {
		/* best-effort */
	}
}

test.describe('invoice duplicate detection (double-payment guard)', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/invoices');
		SLUG = slugFromPage(page);
		H = await authedTenantHeaders(page, SLUG);
	});

	test('a same-vendor same-number invoice flags duplicate + raises an exception', async ({
		page
	}) => {
		const vendor = `DupGuard Vendor ${Date.now()}`;
		const number = `DUP-${Date.now()}`;
		let firstId: string | null = null;
		let secondId: string | null = null;
		try {
			const first = await createInvoice(page, vendor, number);
			firstId = first.id;

			// The collision: same vendor + same invoice number.
			const second = await createInvoice(page, vendor, number);
			secondId = second.id;

			// A mutation recomputes warnings → the duplicate must surface.
			const recomputed = await touchInvoice(page, second.id);
			const types = (recomputed.warnings ?? []).map((w) => w.type);
			expect(types).toContain('duplicate');

			// And a `duplicate` exception is raised pointing at the second invoice.
			const exceptions = await duplicateExceptionsFor(page, second.id);
			expect(exceptions.length).toBeGreaterThan(0);
			expect(exceptions[0].exception_type).toBe('duplicate');
			expect(exceptions[0].status).toBe('open');
		} finally {
			if (secondId) deleteInvoice(secondId);
			if (firstId) deleteInvoice(firstId);
		}
	});

	test('a distinct invoice number for the same vendor does NOT flag duplicate', async ({
		page
	}) => {
		const vendor = `NoDup Vendor ${Date.now()}`;
		let firstId: string | null = null;
		let secondId: string | null = null;
		try {
			const first = await createInvoice(page, vendor, `ND-A-${Date.now()}`);
			firstId = first.id;
			const second = await createInvoice(page, vendor, `ND-B-${Date.now()}`);
			secondId = second.id;

			const recomputed = await touchInvoice(page, second.id);
			const types = (recomputed.warnings ?? []).map((w) => w.type);
			expect(types).not.toContain('duplicate');

			expect((await duplicateExceptionsFor(page, second.id)).length).toBe(0);
		} finally {
			if (secondId) deleteInvoice(secondId);
			if (firstId) deleteInvoice(firstId);
		}
	});
});
