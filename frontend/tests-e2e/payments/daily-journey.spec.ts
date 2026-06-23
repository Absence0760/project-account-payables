import {
	API_BASE,
	authedTenantHeaders,
	expect,
	signInAndWait,
	tenantPsql,
	test
} from '../fixtures/helpers';

/** Patch org settings (admin-scoped). */
async function patchOrg(page: import('@playwright/test').Page, partial: object): Promise<void> {
	await page.request.patch(`${API_BASE}/api/organization`, {
		headers: await authedTenantHeaders(page),
		data: { settings: partial }
	});
}

/**
 * /payments — the AP user's pay-run daily journey through the Runs tab,
 * end to end:
 *
 *   a draft run exists → open the Runs tab → click the run row to open
 *   the detail modal → Execute → confirm the run flips draft → completed
 *   in the modal, the funded invoice flips approved → payment_scheduled,
 *   and the invoice has left the payment queue.
 *
 * The genuine gap this fills: `execute.spec.ts` drives the
 * queue → Review & Pay → Create Draft → (auto-opened) modal → Execute
 * path and asserts statuses via the API. It never exercises the **Runs
 * tab → click a run row → open RunDetailModal → Execute** path, which is
 * how an operator returns to a draft they created earlier (or one a
 * colleague created) and funds it. `queue.spec.ts` stops short of
 * creating any run. This file owns the Runs-tab open-and-execute leg.
 *
 * The mock payment adapter settles synchronously, so by the time the
 * execute response lands the run is `completed`, the payment
 * `completed`, and the invoice `payment_scheduled`. Cleanup reverts via
 * direct SQL — executed runs are append-only by design (no void-run
 * endpoint) and `payment_scheduled` is an immutable invoice status the
 * PATCH endpoint refuses, mirroring `execute.spec.ts`.
 */

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

async function createDraftRun(
	page: import('@playwright/test').Page,
	invoiceId: string,
	method = 'ach'
): Promise<string> {
	const resp = await page.request.post(`${API_BASE}/api/payments/runs`, {
		headers: await authedTenantHeaders(page),
		data: { items: [{ invoice_id: invoiceId, method }] }
	});
	expect(resp.status()).toBe(201);
	return ((await resp.json()) as { id: string }).id;
}

/** Hard-purge a run + its payments (no void-run API by design). */
function deletePaymentRun(runId: string): void {
	tenantPsql(`DELETE FROM payments WHERE payment_run_id='${runId}'`);
	tenantPsql(`DELETE FROM payment_runs WHERE id='${runId}'`);
}

/** Revert an invoice's status (payment_scheduled is immutable to PATCH). */
function resetInvoiceStatus(id: string, status: string): void {
	tenantPsql(`UPDATE invoices SET status='${status}' WHERE id='${id}'`);
}

// Drives the login UI explicitly — opt out of the worker's pre-signed-in
// storage-state default (per fixtures/helpers.ts).
test.use({ storageState: { cookies: [], origins: [] } });

test.describe('/payments pay-run daily journey (Runs tab)', () => {
	// The execute_payment_run maker-checker gate (SoD) blocks the same user
	// who created a run from also executing it. This daily-journey spec uses
	// a single admin session for both steps, so disable SoD for the test and
	// restore it afterwards. The explicit SoD tests live in run-cfo-signoff.spec.ts.
	test.beforeEach(async ({ page, tenantAdmin }) => {
		await signInAndWait(page, tenantAdmin);
		await patchOrg(page, { payments: { require_run_segregation: false } });
	});

	test.afterEach(async ({ page, tenantAdmin }) => {
		await signInAndWait(page, tenantAdmin);
		await patchOrg(page, { payments: { require_run_segregation: true } });
	});

	test('open a draft run from the Runs tab → Execute → run completed + invoice scheduled + leaves queue', async ({
		page,
		tenantAdmin
	}) => {
		// Sign in explicitly (deterministic — the auth store snapshots
		// loggedIn at module-eval time, so a worker's first navigation can
		// race the storage-state default and bounce to /login), then land on
		// the payments page and wait on the real authed signal (the tab bar).
		await signInAndWait(page, tenantAdmin);
		await page.goto('/payments');
		await expect(page.locator('.tab', { hasText: 'Queue' })).toBeVisible();

		const queue = await getQueue(page);
		expect(queue.length).toBeGreaterThan(0);
		const target = queue[0];
		const sourceStatus = await getInvoiceStatus(page, target.id);

		let runId: string | null = null;
		try {
			// A draft run is waiting (created earlier / by a colleague).
			runId = await createDraftRun(page, target.id, 'ach');
			const shortId = runId.slice(0, 8);

			// 1. Open the Runs tab — wait on the real list fetch.
			const runsLoaded = page.waitForResponse(
				(r) =>
					r.url().includes('/api/payments/runs/') &&
					r.request().method() === 'GET' &&
					r.status() === 200
			);
			await page.locator('.tab', { hasText: 'Runs' }).click();
			await runsLoaded;

			// 2. The draft run is listed; its row badge reads "draft".
			const runRow = page
				.locator('table tbody tr', { hasText: shortId })
				.first();
			await expect(runRow).toBeVisible();
			await expect(runRow.locator('.badge')).toHaveText('draft');

			// 3. Open the run detail from the row (RowLink → modal). The
			//    modal loads the run via GET /runs/{id}; wait on it.
			const runDetail = page.waitForResponse(
				(r) =>
					r.url().includes(`/api/payments/runs/${runId}`) &&
					r.request().method() === 'GET' &&
					r.status() === 200
			);
			await runRow
				.getByRole('button', { name: `View payment run ${shortId}` })
				.click();
			await runDetail;

			const modal = page.locator('div.modal[role="dialog"][aria-label="Payment run"]');
			await expect(modal).toBeVisible();
			await expect(modal.locator('.status-badge')).toHaveText('draft');

			// 4. Execute. Wait on the POST /execute success response.
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

			// 5. The modal reloads and the status badge now reads "completed".
			await expect(modal.locator('.status-badge')).toHaveText('completed', {
				timeout: 5_000
			});

			// 6. The invoice flipped approved → payment_scheduled.
			expect(await getInvoiceStatus(page, target.id)).toBe('payment_scheduled');

			// 7. UI truth: close the modal, return to the Queue tab, and
			//    confirm the funded invoice has left the payment queue.
			// The modal has both an ✕ icon (.close-btn) and a "Close"
			// footer button — target the icon unambiguously.
			await modal.locator('.close-btn').click();
			await expect(modal).toBeHidden();

			const queueReload = page.waitForResponse(
				(r) =>
					r.url().includes('/api/payments/queue') &&
					r.request().method() === 'GET' &&
					r.status() === 200
			);
			await page.locator('.tab', { hasText: 'Queue' }).click();
			await queueReload;

			await expect(
				page.locator('table tbody tr', { hasText: target.invoice_number })
			).toHaveCount(0);
		} finally {
			if (runId) deletePaymentRun(runId);
			resetInvoiceStatus(target.id, sourceStatus);
		}
	});
});
