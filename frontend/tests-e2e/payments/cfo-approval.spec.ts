import { execFileSync } from 'node:child_process';

import { expect, test } from '@playwright/test';

import { ACME_CFO, signInAndWait } from '../fixtures/helpers';

const API_BASE = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

async function authToken(page: import('@playwright/test').Page) {
	const t = await page.evaluate(() => localStorage.getItem('auth_token'));
	if (!t) throw new Error('not signed in');
	return t;
}

async function patchOrg(page: import('@playwright/test').Page, partial: object) {
	const token = await authToken(page);
	await page.request.patch(`${API_BASE}/api/organization`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
		data: { settings: partial }
	});
}

async function createApprovedInvoice(
	page: import('@playwright/test').Page,
	suffix: string,
	amount: number
): Promise<string> {
	const token = await authToken(page);
	const resp = await page.request.post(`${API_BASE}/api/invoices`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
		data: {
			vendor: 'E2E CFO Vendor',
			invoice_number: `E2E-CFO-${suffix}`,
			amount,
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
): Promise<{ id: string; requires_cfo_approval: boolean }> {
	const token = await authToken(page);
	const resp = await page.request.post(`${API_BASE}/api/payments/runs`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
		data: { items: [{ invoice_id: invoiceId, method: 'ach' }] }
	});
	const body = (await resp.json()) as { id: string; requires_cfo_approval: boolean };
	return body;
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
 * CFO sign-off on high-value payment runs. The threshold lives in the
 * org settings (`payments.cfo_approval_above`). Each test sets a known
 * threshold and resets it in `finally` so the suite is hermetic.
 */
test.describe('/payments — CFO approval gate (acme)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
		await patchOrg(page, { payments: { cfo_approval_above: 1000 } });
	});

	test.afterEach(async ({ page }) => {
		// Sign back in as admin in case the test signed in as CFO; clearing
		// the threshold leaves the tenant clean for the rest of the suite.
		await signInAndWait(page);
		await patchOrg(page, { payments: { cfo_approval_above: null } });
	});

	test('runs over the threshold land with requires_cfo_approval=true', async ({ page }) => {
		const invoiceId = await createApprovedInvoice(page, `over-${Date.now()}`, 5000);
		try {
			const run = await createRun(page, invoiceId);
			expect(run.requires_cfo_approval).toBe(true);
		} finally {
			hardDeleteInvoice(invoiceId);
		}
	});

	test('runs at or below the threshold do not require CFO approval', async ({ page }) => {
		const invoiceId = await createApprovedInvoice(page, `under-${Date.now()}`, 500);
		try {
			const run = await createRun(page, invoiceId);
			expect(run.requires_cfo_approval).toBe(false);
		} finally {
			hardDeleteInvoice(invoiceId);
		}
	});

	test('execute is rejected with 403 while CFO approval pending', async ({ page }) => {
		const invoiceId = await createApprovedInvoice(page, `block-${Date.now()}`, 5000);
		let runId: string | null = null;
		try {
			const run = await createRun(page, invoiceId);
			runId = run.id;
			expect(run.requires_cfo_approval).toBe(true);

			const token = await authToken(page);
			const resp = await page.request.post(
				`${API_BASE}/api/payments/runs/${runId}/execute`,
				{ headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' } }
			);
			expect(resp.status()).toBe(403);
			const body = (await resp.json()) as { detail: string };
			expect(body.detail).toContain('CFO');
		} finally {
			if (runId) deletePaymentRun(runId);
			hardDeleteInvoice(invoiceId);
		}
	});

	test('non-CFO actor cannot approve (403)', async ({ page }) => {
		const invoiceId = await createApprovedInvoice(page, `nocfo-${Date.now()}`, 5000);
		let runId: string | null = null;
		try {
			const run = await createRun(page, invoiceId);
			runId = run.id;

			const token = await authToken(page);
			const resp = await page.request.post(
				`${API_BASE}/api/payments/runs/${runId}/approve`,
				{ headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' } }
			);
			// Acme admin doesn't hold the CFO role — gate is `require_roles(ROLE_CFO)`.
			expect(resp.status()).toBe(403);
		} finally {
			if (runId) deletePaymentRun(runId);
			hardDeleteInvoice(invoiceId);
		}
	});

	test('CFO approves → execute then runs end-to-end', async ({ page }) => {
		const invoiceId = await createApprovedInvoice(page, `flow-${Date.now()}`, 5000);
		let runId: string | null = null;
		try {
			const run = await createRun(page, invoiceId);
			runId = run.id;

			// Sign in as the CFO and approve.
			await signInAndWait(page, ACME_CFO);
			const token = await authToken(page);
			const approveResp = await page.request.post(
				`${API_BASE}/api/payments/runs/${runId}/approve`,
				{ headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' } }
			);
			expect(approveResp.status()).toBe(200);
			const approveBody = (await approveResp.json()) as { cfo_approved_by: string };
			expect(approveBody.cfo_approved_by).toBeTruthy();

			// Now execute (still as the CFO — the standard payments role set
			// includes CFO). Run flips to completed via the mock adapter.
			const execResp = await page.request.post(
				`${API_BASE}/api/payments/runs/${runId}/execute`,
				{ headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' } }
			);
			expect(execResp.status()).toBe(200);
			expect(((await execResp.json()) as { status: string }).status).toBe('completed');
		} finally {
			if (runId) deletePaymentRun(runId);
			hardDeleteInvoice(invoiceId);
		}
	});

	test('approving a run that does not require approval is rejected with 409', async ({
		page
	}) => {
		const invoiceId = await createApprovedInvoice(page, `noreq-${Date.now()}`, 100);
		let runId: string | null = null;
		try {
			const run = await createRun(page, invoiceId);
			runId = run.id;
			expect(run.requires_cfo_approval).toBe(false);

			await signInAndWait(page, ACME_CFO);
			const token = await authToken(page);
			const resp = await page.request.post(
				`${API_BASE}/api/payments/runs/${runId}/approve`,
				{ headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' } }
			);
			expect(resp.status()).toBe(409);
		} finally {
			if (runId) deletePaymentRun(runId);
			hardDeleteInvoice(invoiceId);
		}
	});
});

test.describe('/payments — remittance PDF (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
	});

	test('GET /payments/{id}/remittance returns a PDF for completed payments', async ({
		page
	}) => {
		const invoiceId = await createApprovedInvoice(page, `pdf-${Date.now()}`, 250);
		let runId: string | null = null;
		try {
			const run = await createRun(page, invoiceId);
			runId = run.id;
			const token = await authToken(page);
			await page.request.post(`${API_BASE}/api/payments/runs/${runId}/execute`, {
				headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
			});

			const detailResp = await page.request.get(
				`${API_BASE}/api/payments/runs/${runId}`,
				{ headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' } }
			);
			const detail = (await detailResp.json()) as {
				payments: Array<{ id: string; status: string }>;
			};
			const payment = detail.payments[0];
			expect(payment.status).toBe('completed');

			const pdfResp = await page.request.get(
				`${API_BASE}/api/payments/${payment.id}/remittance`,
				{ headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' } }
			);
			expect(pdfResp.status()).toBe(200);
			expect(pdfResp.headers()['content-type']).toContain('application/pdf');
			expect(pdfResp.headers()['content-disposition']).toContain('attachment');

			const buf = await pdfResp.body();
			// PDF magic byte signature.
			expect(buf.slice(0, 5).toString()).toBe('%PDF-');
			expect(buf.length).toBeGreaterThan(500); // not an empty stub
		} finally {
			if (runId) deletePaymentRun(runId);
			hardDeleteInvoice(invoiceId);
		}
	});
});
