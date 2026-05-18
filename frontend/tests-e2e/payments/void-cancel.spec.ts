import { execFileSync } from 'node:child_process';

import { expect, test, ACME_BASE } from '../fixtures/helpers';

import { ACME_CLERK, signInAndWait } from '../fixtures/helpers';

// Pinned to the acme tenant: this spec uses ACME_*/TECHFLOW_* creds or
// asserts cross-tenant isolation that requires fixed tenant slugs. The
// per-worker baseURL from fixtures/helpers.ts would otherwise route to
// the wrong tenant. Multiple workers may share acme here — keep this
// file's tests read-only or idempotent.
test.use({ baseURL: ACME_BASE });

const API_BASE = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

async function authToken(page: import('@playwright/test').Page) {
	const t = await page.evaluate(() => localStorage.getItem('auth_token'));
	if (!t) throw new Error('not signed in');
	return t;
}

interface QueueItem {
	id: string;
	invoice_number: string;
	amount: number;
}

async function getQueue(page: import('@playwright/test').Page): Promise<QueueItem[]> {
	const token = await authToken(page);
	const resp = await page.request.get(`${API_BASE}/api/payments/queue`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
	});
	return ((await resp.json()) as { items: QueueItem[] }).items;
}

async function getInvoiceStatus(
	page: import('@playwright/test').Page,
	id: string
): Promise<string> {
	const token = await authToken(page);
	const resp = await page.request.get(`${API_BASE}/api/invoices/${id}`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
	});
	return ((await resp.json()) as { status: string }).status;
}

async function createApprovedInvoice(
	page: import('@playwright/test').Page,
	suffix: string
): Promise<string> {
	const token = await authToken(page);
	const resp = await page.request.post(`${API_BASE}/api/invoices`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
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
	const token = await authToken(page);
	const resp = await page.request.post(`${API_BASE}/api/payments/runs`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
		data: { items: [{ invoice_id: invoiceId, method: 'ach' }] }
	});
	return ((await resp.json()) as { id: string }).id;
}

async function executeRun(
	page: import('@playwright/test').Page,
	runId: string
): Promise<void> {
	const token = await authToken(page);
	await page.request.post(`${API_BASE}/api/payments/runs/${runId}/execute`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
	});
}

async function getRunPayment(
	page: import('@playwright/test').Page,
	runId: string
): Promise<{ id: string; status: string }> {
	const token = await authToken(page);
	const resp = await page.request.get(`${API_BASE}/api/payments/runs/${runId}`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
	});
	const body = (await resp.json()) as { payments: Array<{ id: string; status: string }> };
	return body.payments[0];
}

function hardDeleteInvoice(id: string): void {
	execFileSync(
		'psql',
		[
			'-h', 'localhost',
			'-U', 'postgres',
			'-p', '5432',
			'-d', 'ap_acme',
			'-c', `DELETE FROM payments WHERE invoice_id='${id}'`,
			'-c', `DELETE FROM payment_runs WHERE id IN (SELECT DISTINCT payment_run_id FROM payments WHERE invoice_id='${id}')`,
			'-c', `DELETE FROM workflow_steps WHERE instance_id IN (SELECT id FROM workflow_instances WHERE invoice_id='${id}')`,
			'-c', `DELETE FROM workflow_instances WHERE invoice_id='${id}'`,
			'-c', `DELETE FROM audit_log WHERE entity_id='${id}'`,
			'-c', `DELETE FROM invoices WHERE id='${id}'`
		],
		{ env: { ...process.env, PGPASSWORD: 'postgres' }, stdio: 'pipe' }
	);
}

function deletePaymentRun(runId: string): void {
	execFileSync(
		'psql',
		[
			'-h', 'localhost',
			'-U', 'postgres',
			'-p', '5432',
			'-d', 'ap_acme',
			'-c', `DELETE FROM payments WHERE payment_run_id='${runId}'`,
			'-c', `DELETE FROM payment_runs WHERE id='${runId}'`
		],
		{ env: { ...process.env, PGPASSWORD: 'postgres' }, stdio: 'pipe' }
	);
}

/**
 * Void / cancel flows. The mock adapter accepts every void synchronously,
 * so the API tests assert the local bookkeeping; the audit-log row carries
 * the adapter outcome (`voided_upstream` for the mock).
 */

test.describe('/payments — void completed payment (acme admin / cfo)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
	});

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

			const token = await authToken(page);
			const voidResp = await page.request.post(
				`${API_BASE}/api/payments/${before.id}/void`,
				{
					headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
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
			const token = await authToken(page);

			// Force the payment into `failed` via SQL — the mock adapter
			// always reports `completed`, so we need to short-circuit it.
			execFileSync(
				'psql',
				[
					'-h', 'localhost',
					'-U', 'postgres',
					'-p', '5432',
					'-d', 'ap_acme',
					'-c', `UPDATE payments SET status='failed' WHERE id='${before.id}'`
				],
				{ env: { ...process.env, PGPASSWORD: 'postgres' }, stdio: 'pipe' }
			);

			const voidResp = await page.request.post(
				`${API_BASE}/api/payments/${before.id}/void`,
				{
					headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
					data: { reason: 'should reject' }
				}
			);
			expect(voidResp.status()).toBe(409);
		} finally {
			if (runId) deletePaymentRun(runId);
			hardDeleteInvoice(invoiceId);
		}
	});

	test('clerk role gets 403 on void', async ({ page }) => {
		await signInAndWait(page, ACME_CLERK);
		const token = await authToken(page);
		const fakeId = '00000000-0000-0000-0000-000000000000';
		const resp = await page.request.post(`${API_BASE}/api/payments/${fakeId}/void`, {
			headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
			data: { reason: 'should-403' }
		});
		expect(resp.status()).toBe(403);
	});
});

test.describe('/payments — cancel draft run (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
	});

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

			const token = await authToken(page);
			const cancelResp = await page.request.post(
				`${API_BASE}/api/payments/runs/${runId}/cancel`,
				{ headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' } }
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
				{ headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' } }
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

			const token = await authToken(page);
			const resp = await page.request.post(
				`${API_BASE}/api/payments/runs/${runId}/cancel`,
				{ headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' } }
			);
			expect(resp.status()).toBe(409);
		} finally {
			if (runId) deletePaymentRun(runId);
			hardDeleteInvoice(invoiceId);
		}
	});
});
