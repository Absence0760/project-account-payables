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
	const body = (await resp.json()) as { items: QueueItem[] };
	return body.items;
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

/** Disable / re-enable the maker-checker run-segregation gate. */
async function patchOrg(
	page: import('@playwright/test').Page,
	partial: object
): Promise<void> {
	await page.request.patch(`${API_BASE}/api/organization`, {
		headers: await authedTenantHeaders(page),
		data: { settings: partial }
	});
}

/**
 * Force-revert an invoice's status. PATCH /invoices rejects updates to
 * IMMUTABLE_STATUSES (payment_scheduled, paid, posted_in_erp, ...) by
 * design — execute moves the invoice into one of those, and there is
 * no UI path back. Direct SQL is the only revertible option for tests.
 */
function resetInvoiceStatus(id: string, status: string): void {
	tenantPsql(`UPDATE invoices SET status='${status}' WHERE id='${id}'`);
}

/**
 * Hard-purge a payment run + its payments. The product API has no
 * "void run" endpoint by design (executed runs are append-only for
 * audit), so direct SQL is the only revertible path. psql lives on dev
 * workstations and in the CI backend container.
 */
function deletePaymentRun(runId: string): void {
	tenantPsql(`DELETE FROM payments WHERE payment_run_id='${runId}'`);
	tenantPsql(`DELETE FROM payment_runs WHERE id='${runId}'`);
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
 *
 * SoD note: `execute_payment_run` enforces maker-checker (the user who
 * creates a run cannot also execute it). Tests that use the same admin
 * session for both operations disable SoD via `require_run_segregation:
 * false` — a legitimate per-org single-operator configuration — and reset
 * it afterward. Tests that explicitly cover SoD live in run-cfo-signoff.spec.ts.
 *
 * Arming note: Execute is a two-click armed commit (`docs/followups.md` item 7),
 * matching the `Cancel run` sibling in the same footer. The first click ARMS —
 * the button relabels from "Execute · <amount>" to "Confirm execute · <amount>"
 * — and only the second click moves money. Every UI execute below clicks twice,
 * and the label change is a real DOM signal to wait on, never a timer.
 */

/** Click the footer's Execute, then its armed confirmation. */
async function armAndExecute(modal: import('@playwright/test').Locator): Promise<void> {
	await modal.getByRole('button', { name: /^Execute/ }).click();
	// The armed control is a DISTINCT accessible name, so this wait is a real
	// signal that the arm landed — no sleep, no retry.
	const confirm = modal.getByRole('button', { name: /^Confirm execute/ });
	await expect(confirm).toBeVisible();
	await confirm.click();
}

test.describe('/payments execute', () => {
	test.beforeEach(async ({ page }) => {
		// Disable maker-checker SoD so a single admin can both create and
		// execute a run within the same UI session. This is a valid per-org
		// configuration (single-operator accounts). SoD enforcement is tested
		// in run-cfo-signoff.spec.ts.
		await patchOrg(page, { payments: { require_run_segregation: false } });
		// Wait for the page to settle before navigating — avoids ERR_ABORTED
		// when the pre-navigated tenant root still has in-flight HMR/redirect
		// requests outstanding from the storageState fixture's initial goto.
		await page.waitForLoadState('networkidle');
		await page.goto('/payments');
		await page.waitForLoadState('networkidle');
	});

	test.afterEach(async ({ page, tenantAdmin }) => {
		// Restore the admin session (a test may have switched to a different role)
		// and re-enable SoD so the org setting is hermetic across the suite.
		await signInAndWait(page, tenantAdmin);
		await patchOrg(page, { payments: { require_run_segregation: true } });
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
			await armAndExecute(modal);
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

	test('a single Execute click ARMS and moves no money', async ({ page }) => {
		// Regression guard for `docs/followups.md` item 7: Execute used to be a
		// single unarmed click on the one irreversible money-moving control in
		// the app. The first click must only arm.
		const queue = await getQueue(page);
		expect(queue.length).toBeGreaterThan(0);
		const target = queue[0];
		const sourceStatus = await getInvoiceStatus(page, target.id);
		const headers = await authedTenantHeaders(page);

		const createResp = await page.request.post(`${API_BASE}/api/payments/runs`, {
			headers,
			data: { items: [{ invoice_id: target.id, method: 'ach' }] }
		});
		const runId = ((await createResp.json()) as { id: string }).id;

		// Count every execute POST the page fires. A request is initiated
		// synchronously inside the click handler, so once the armed control has
		// rendered we know whether the click called the API — no sleep needed.
		let executeCalls = 0;
		page.on('request', (req) => {
			if (req.method() === 'POST' && req.url().includes(`/runs/${runId}/execute`)) {
				executeCalls += 1;
			}
		});

		try {
			await page.reload();
			await page.waitForLoadState('networkidle');
			await page.locator('.tab', { hasText: 'Runs' }).click();
			await page
				.getByRole('button', { name: `View payment run ${runId.slice(0, 8)}` })
				.click();

			const modal = page.locator('div.modal[role="dialog"][aria-label="Payment run"]');
			await expect(modal).toBeVisible();
			await expect(modal.locator('.status-badge')).toHaveText('draft');

			// ONE click: arms only.
			await modal.getByRole('button', { name: /^Execute/ }).click();
			await expect(modal.getByRole('button', { name: /^Confirm execute/ })).toBeVisible();
			await expect(modal.getByTestId('execute-armed-note')).toBeVisible();
			expect(executeCalls).toBe(0);

			// Server-side proof: the run is untouched.
			const stillDraft = await page.request.get(`${API_BASE}/api/payments/runs/${runId}`, {
				headers
			});
			expect(((await stillDraft.json()) as { status: string }).status).toBe('draft');
		} finally {
			deletePaymentRun(runId);
			resetInvoiceStatus(target.id, sourceStatus);
		}
	});

	test('arming Cancel run disarms Execute (only one commit is ever armed)', async ({
		page
	}) => {
		const queue = await getQueue(page);
		expect(queue.length).toBeGreaterThan(0);
		const target = queue[0];
		const sourceStatus = await getInvoiceStatus(page, target.id);
		const headers = await authedTenantHeaders(page);

		const createResp = await page.request.post(`${API_BASE}/api/payments/runs`, {
			headers,
			data: { items: [{ invoice_id: target.id, method: 'ach' }] }
		});
		const runId = ((await createResp.json()) as { id: string }).id;

		try {
			await page.reload();
			await page.waitForLoadState('networkidle');
			await page.locator('.tab', { hasText: 'Runs' }).click();
			await page
				.getByRole('button', { name: `View payment run ${runId.slice(0, 8)}` })
				.click();

			const modal = page.locator('div.modal[role="dialog"][aria-label="Payment run"]');
			await expect(modal).toBeVisible();

			await modal.getByRole('button', { name: /^Execute/ }).click();
			await expect(modal.getByRole('button', { name: /^Confirm execute/ })).toBeVisible();

			// Arming the sibling retracts the money button — two armed red
			// controls side by side is how a mis-click becomes the wrong
			// irreversible action.
			await modal.getByRole('button', { name: 'Cancel run' }).click();
			await expect(modal.getByRole('button', { name: 'Confirm cancel' })).toBeVisible();
			await expect(modal.getByRole('button', { name: /^Confirm execute/ })).toBeHidden();
			await expect(modal.getByRole('button', { name: /^Execute/ })).toBeVisible();
			await expect(modal.getByTestId('execute-armed-note')).toBeHidden();
		} finally {
			deletePaymentRun(runId);
			resetInvoiceStatus(target.id, sourceStatus);
		}
	});

	test('Cannot execute the same run twice — second call returns 409', async ({ page }) => {
		const queue = await getQueue(page);
		expect(queue.length).toBeGreaterThan(0);
		const target = queue[0];
		const sourceStatus = await getInvoiceStatus(page, target.id);
		const headers = await authedTenantHeaders(page);

		// Create the run via the API directly (UI path is covered above).
		const createResp = await page.request.post(`${API_BASE}/api/payments/runs`, {
			headers,
			data: { items: [{ invoice_id: target.id, method: 'ach' }] }
		});
		const runId = ((await createResp.json()) as { id: string }).id;

		try {
			// First execute: 200 (SoD disabled in beforeEach so admin can both
			// create and execute).
			const first = await page.request.post(
				`${API_BASE}/api/payments/runs/${runId}/execute`,
				{ headers }
			);
			expect(first.status()).toBe(200);

			// Second execute: 409 because run.status is no longer 'draft'.
			const second = await page.request.post(
				`${API_BASE}/api/payments/runs/${runId}/execute`,
				{ headers }
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
		const headers = await authedTenantHeaders(page);

		const createResp = await page.request.post(`${API_BASE}/api/payments/runs`, {
			headers,
			data: { items: [{ invoice_id: target.id, method: 'wire' }] }
		});
		const runId = ((await createResp.json()) as { id: string }).id;

		try {
			await page.request.post(`${API_BASE}/api/payments/runs/${runId}/execute`, {
				headers
			});

			const queueAfter = await getQueue(page);
			expect(queueAfter.find((q) => q.id === target.id)).toBeUndefined();
		} finally {
			deletePaymentRun(runId);
			resetInvoiceStatus(target.id, sourceStatus);
		}
	});
});
