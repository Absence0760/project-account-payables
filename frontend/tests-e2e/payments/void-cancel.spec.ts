import {
	API_BASE,
	authedTenantHeaders,
	expect,
	signInAndWait,
	tenantPsql,
	test
} from '../fixtures/helpers';

interface QueueItem {
	id: string;
	invoice_number: string;
	amount: number;
}

async function getQueue(page: import('@playwright/test').Page): Promise<QueueItem[]> {
	const resp = await page.request.get(`${API_BASE}/api/payments/queue`, {
		headers: await authedTenantHeaders(page)
	});
	return ((await resp.json()) as { items: QueueItem[] }).items;
}

async function getInvoiceStatus(
	page: import('@playwright/test').Page,
	id: string
): Promise<string> {
	const resp = await page.request.get(`${API_BASE}/api/invoices/${id}`, {
		headers: await authedTenantHeaders(page)
	});
	return ((await resp.json()) as { status: string }).status;
}

async function createApprovedInvoice(
	page: import('@playwright/test').Page,
	suffix: string
): Promise<string> {
	const resp = await page.request.post(`${API_BASE}/api/invoices`, {
		headers: await authedTenantHeaders(page),
		data: {
			vendor: 'E2E Void/Cancel Vendor',
			invoice_number: `E2E-VC-${suffix}`,
			amount: 525.0,
			currency: 'USD',
			status: 'approved'
		}
	});
	expect(resp.status()).toBe(201);
	return ((await resp.json()) as { id: string }).id;
}

async function createRun(
	page: import('@playwright/test').Page,
	invoiceId: string
): Promise<string> {
	const resp = await page.request.post(`${API_BASE}/api/payments/runs`, {
		headers: await authedTenantHeaders(page),
		data: { items: [{ invoice_id: invoiceId, method: 'ach' }] }
	});
	return ((await resp.json()) as { id: string }).id;
}

async function executeRun(
	page: import('@playwright/test').Page,
	runId: string
): Promise<void> {
	await page.request.post(`${API_BASE}/api/payments/runs/${runId}/execute`, {
		headers: await authedTenantHeaders(page)
	});
}

async function getRunPayment(
	page: import('@playwright/test').Page,
	runId: string
): Promise<{ id: string; status: string }> {
	const resp = await page.request.get(`${API_BASE}/api/payments/runs/${runId}`, {
		headers: await authedTenantHeaders(page)
	});
	const body = (await resp.json()) as { payments: Array<{ id: string; status: string }> };
	return body.payments[0];
}

function hardDeleteInvoice(id: string): void {
	tenantPsql(`DELETE FROM payments WHERE invoice_id='${id}'`);
	tenantPsql(
		`DELETE FROM payment_runs WHERE id IN (SELECT DISTINCT payment_run_id FROM payments WHERE invoice_id='${id}')`
	);
	tenantPsql(
		`DELETE FROM workflow_steps WHERE instance_id IN (SELECT id FROM workflow_instances WHERE invoice_id='${id}')`
	);
	tenantPsql(`DELETE FROM workflow_instances WHERE invoice_id='${id}'`);
	// audit_log is append-only (a BEFORE DELETE trigger raises) — once an
	// invoice runs through execute/void it carries invoice.payment_scheduled
	// / invoice.voided_return_to_approved rows that cannot be deleted. Leave
	// them orphaned (no FK to invoices); deleting the invoice is enough.
	tenantPsql(`DELETE FROM invoices WHERE id='${id}'`);
}

function deletePaymentRun(runId: string): void {
	tenantPsql(`DELETE FROM payments WHERE payment_run_id='${runId}'`);
	tenantPsql(`DELETE FROM payment_runs WHERE id='${runId}'`);
}

/**
 * Void / cancel flows. The mock adapter accepts every void synchronously,
 * so the API tests assert the local bookkeeping; the audit-log row carries
 * the adapter outcome (`voided_upstream` for the mock).
 */

test.describe('/payments — void completed payment', () => {
	test('void flips payment to voided, invoice back to approved, audit row written', async ({
		page
	}) => {
		const stamp = Date.now();
		const invoiceId = await createApprovedInvoice(page, `void-${stamp}`);
		let runId: string | null = null;
		try {
			runId = await createRun(page, invoiceId);
			await executeRun(page, runId);
			const before = await getRunPayment(page, runId);
			expect(before.status).toBe('completed');

			const voidResp = await page.request.post(
				`${API_BASE}/api/payments/${before.id}/void`,
				{
					headers: await authedTenantHeaders(page),
					data: { reason: 'e2e: testing void path' }
				}
			);
			expect(voidResp.status()).toBe(200);
			const body = (await voidResp.json()) as { status: string };
			expect(body.status).toBe('voided');

			// Invoice flipped back to `approved` and re-enters the queue.
			expect(await getInvoiceStatus(page, invoiceId)).toBe('approved');
			const queue = await getQueue(page);
			expect(queue.some((q) => q.id === invoiceId)).toBe(true);
		} finally {
			if (runId) deletePaymentRun(runId);
			hardDeleteInvoice(invoiceId);
		}
	});

	test('voiding a failed payment is rejected with 409', async ({ page }) => {
		const stamp = Date.now();
		const invoiceId = await createApprovedInvoice(page, `void-failed-${stamp}`);
		let runId: string | null = null;
		try {
			runId = await createRun(page, invoiceId);
			await executeRun(page, runId);
			const before = await getRunPayment(page, runId);

			// Force the payment into `failed` via SQL — the mock adapter
			// always reports `completed`, so we need to short-circuit it.
			tenantPsql(`UPDATE payments SET status='failed' WHERE id='${before.id}'`);

			const voidResp = await page.request.post(
				`${API_BASE}/api/payments/${before.id}/void`,
				{
					headers: await authedTenantHeaders(page),
					data: { reason: 'should reject' }
				}
			);
			expect(voidResp.status()).toBe(409);
		} finally {
			if (runId) deletePaymentRun(runId);
			hardDeleteInvoice(invoiceId);
		}
	});

	test('clerk role gets 403 on void', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);
		const fakeId = '00000000-0000-0000-0000-000000000000';
		const resp = await page.request.post(`${API_BASE}/api/payments/${fakeId}/void`, {
			headers: await authedTenantHeaders(page),
			data: { reason: 'should-403' }
		});
		expect(resp.status()).toBe(403);
	});
});

test.describe('/payments — cancel draft run', () => {
	test('cancel flips draft run to cancelled and releases its invoices', async ({ page }) => {
		const stamp = Date.now();
		const invoiceId = await createApprovedInvoice(page, `cancel-${stamp}`);
		let runId: string | null = null;
		try {
			runId = await createRun(page, invoiceId);

			// Invoice is pinned to the draft run; not yet flipped to scheduled.
			expect(await getInvoiceStatus(page, invoiceId)).toBe('approved');
			// But the queue temporarily hides it because there's a non-completed
			// payment row pointing at it. Cancel should re-surface it.

			const headers = await authedTenantHeaders(page);
			const cancelResp = await page.request.post(
				`${API_BASE}/api/payments/runs/${runId}/cancel`,
				{ headers }
			);
			expect(cancelResp.status()).toBe(200);
			const body = (await cancelResp.json()) as {
				status: string;
				released_invoices: number;
			};
			expect(body.status).toBe('cancelled');
			expect(body.released_invoices).toBe(1);

			// Run still readable; status reflects the cancel.
			const detailResp = await page.request.get(
				`${API_BASE}/api/payments/runs/${runId}`,
				{ headers }
			);
			const detail = (await detailResp.json()) as {
				status: string;
				payments: unknown[];
			};
			expect(detail.status).toBe('cancelled');
			expect(detail.payments).toHaveLength(0);

			// Invoice is back in the queue.
			const queue = await getQueue(page);
			expect(queue.some((q) => q.id === invoiceId)).toBe(true);
		} finally {
			if (runId) deletePaymentRun(runId);
			hardDeleteInvoice(invoiceId);
		}
	});

	test('cannot cancel a run that has already executed', async ({ page }) => {
		const stamp = Date.now();
		const invoiceId = await createApprovedInvoice(page, `noex-${stamp}`);
		let runId: string | null = null;
		try {
			runId = await createRun(page, invoiceId);
			await executeRun(page, runId);

			const resp = await page.request.post(
				`${API_BASE}/api/payments/runs/${runId}/cancel`,
				{ headers: await authedTenantHeaders(page) }
			);
			expect(resp.status()).toBe(409);
		} finally {
			if (runId) deletePaymentRun(runId);
			hardDeleteInvoice(invoiceId);
		}
	});
});
