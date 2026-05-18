import { execFileSync } from 'node:child_process';

import { expect, test } from '../fixtures/helpers';

import { signInAndWait } from '../fixtures/helpers';

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
	const body = (await resp.json()) as { items: QueueItem[] };
	return body.items;
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

/**
 * Force-revert an invoice's status. PATCH /invoices rejects updates to
 * IMMUTABLE_STATUSES (payment_scheduled, paid, posted_in_erp, ...) by
 * design — execute moves the invoice into one of those, and there is
 * no UI path back. Direct SQL is the only revertible option for tests.
 */
function resetInvoiceStatus(id: string, status: string): void {
	execFileSync(
		'psql',
		[
			'-h',
			'localhost',
			'-U',
			'postgres',
			'-p',
			'5432',
			'-d',
			'ap_acme',
			'-c',
			`UPDATE invoices SET status='${status}' WHERE id='${id}'`
		],
		{ env: { ...process.env, PGPASSWORD: 'postgres' }, stdio: 'pipe' }
	);
}

/**
 * Hard-purge a payment run + its payments. The product API has no
 * "void run" endpoint by design (executed runs are append-only for
 * audit), so direct SQL is the only revertible path. psql lives on dev
 * workstations and in the CI backend container.
 */
function deletePaymentRun(runId: string): void {
	execFileSync(
		'psql',
		[
			'-h',
			'localhost',
			'-U',
			'postgres',
			'-p',
			'5432',
			'-d',
			'ap_acme',
			'-c',
			`DELETE FROM payments WHERE payment_run_id='${runId}'`,
			'-c',
			`DELETE FROM payment_runs WHERE id='${runId}'`
		],
		{ env: { ...process.env, PGPASSWORD: 'postgres' }, stdio: 'pipe' }
	);
}

/**
 * /payments — full Create-Draft → Execute round-trip via the mock
 * adapter. The mock returns `completed` synchronously, so by the time
 * the execute response lands:
 *   - run.status        flips draft → completed
 *   - payment.status    flips pending → completed
 *   - invoice.status    flips approved → payment_scheduled
 *
 * Each test reverts via direct DB cleanup + invoice PATCH back to the
 * source status.
 */

test.describe('/payments execute (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
		await page.goto('/payments');
		await page.waitForLoadState('networkidle');
	});

	test('Create Draft → Execute flips run + payment + invoice statuses', async ({
		page
	}) => {
		const queue = await getQueue(page);
		expect(queue.length).toBeGreaterThan(0);
		const target = queue[0];
		const sourceStatus = await getInvoiceStatus(page, target.id);

		let runId: string | null = null;
		try {
			// Select first queue row and Review & Pay.
			await page
				.locator('table tbody tr', { hasText: target.invoice_number })
				.first()
				.locator('input[type="checkbox"]')
				.check();
			await page.locator('.pay-bar').getByRole('button', { name: 'Review & Pay' }).click();

			// Create the draft via the Review panel button. The frontend
			// then opens the RunDetailModal automatically.
			const runCreated = page.waitForResponse(
				(r) =>
					r.url().endsWith('/api/payments/runs') &&
					r.request().method() === 'POST' &&
					r.status() === 201
			);
			await page.locator('.review-panel .btn-execute').click();
			const createdResp = await runCreated;
			runId = ((await createdResp.json()) as { id: string }).id;

			const modal = page.locator('div.modal[role="dialog"][aria-label="Payment run"]');
			await expect(modal).toBeVisible();
			await expect(modal.locator('.status-badge')).toHaveText('draft');

			// Execute.
			const executed = page.waitForResponse(
				(r) =>
					r.url().includes(`/api/payments/runs/${runId}/execute`) &&
					r.request().method() === 'POST' &&
					r.status() === 200
			);
			await modal.getByRole('button', { name: /^Execute/ }).click();
			const execResp = await executed;
			const execBody = (await execResp.json()) as {
				status: string;
				payments_completed: number;
			};
			expect(execBody.status).toBe('completed');
			expect(execBody.payments_completed).toBeGreaterThan(0);

			// Modal reloads; the status badge now reads "completed".
			await expect(modal.locator('.status-badge')).toHaveText('completed', {
				timeout: 5_000
			});

			// Invoice status flipped per the execute logic in payments.py.
			const after = await getInvoiceStatus(page, target.id);
			expect(after).toBe('payment_scheduled');
		} finally {
			if (runId) deletePaymentRun(runId);
			resetInvoiceStatus(target.id, sourceStatus);
		}
	});

	test('Cannot execute the same run twice — second call returns 409', async ({ page }) => {
		const queue = await getQueue(page);
		expect(queue.length).toBeGreaterThan(0);
		const target = queue[0];
		const sourceStatus = await getInvoiceStatus(page, target.id);
		const token = await authToken(page);

		// Create the run via the API directly (UI path is covered above).
		const createResp = await page.request.post(`${API_BASE}/api/payments/runs`, {
			headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
			data: { items: [{ invoice_id: target.id, method: 'ach' }] }
		});
		const runId = ((await createResp.json()) as { id: string }).id;

		try {
			// First execute: 200.
			const first = await page.request.post(
				`${API_BASE}/api/payments/runs/${runId}/execute`,
				{ headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' } }
			);
			expect(first.status()).toBe(200);

			// Second execute: 409 because run.status is no longer 'draft'.
			const second = await page.request.post(
				`${API_BASE}/api/payments/runs/${runId}/execute`,
				{ headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' } }
			);
			expect(second.status()).toBe(409);
		} finally {
			deletePaymentRun(runId);
			resetInvoiceStatus(target.id, sourceStatus);
		}
	});

	test('After execute, the queue no longer contains the paid invoice', async ({ page }) => {
		const queueBefore = await getQueue(page);
		expect(queueBefore.length).toBeGreaterThan(0);
		const target = queueBefore[0];
		const sourceStatus = await getInvoiceStatus(page, target.id);
		const token = await authToken(page);

		const createResp = await page.request.post(`${API_BASE}/api/payments/runs`, {
			headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
			data: { items: [{ invoice_id: target.id, method: 'wire' }] }
		});
		const runId = ((await createResp.json()) as { id: string }).id;

		try {
			await page.request.post(`${API_BASE}/api/payments/runs/${runId}/execute`, {
				headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
			});

			const queueAfter = await getQueue(page);
			expect(queueAfter.find((q) => q.id === target.id)).toBeUndefined();
		} finally {
			deletePaymentRun(runId);
			resetInvoiceStatus(target.id, sourceStatus);
		}
	});
});
