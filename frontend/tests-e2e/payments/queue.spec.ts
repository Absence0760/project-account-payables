import { execFileSync } from 'node:child_process';

import { expect, test, ACME_BASE } from '../fixtures/helpers';

import { signInAndWait } from '../fixtures/helpers';

// Pinned to the acme tenant: this spec talks to acme directly
// (X-Tenant-Slug: 'acme' headers, ap_acme psql calls, hardcoded URLs).
// The per-worker baseURL from fixtures/helpers.ts would route to
// the wrong tenant. Multiple workers may share acme here — keep
// this file's tests read-only or idempotent.
test.use({ baseURL: ACME_BASE });

const API_BASE = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

async function authToken(page: import('@playwright/test').Page) {
	const t = await page.evaluate(() => localStorage.getItem('auth_token'));
	if (!t) throw new Error('not signed in');
	return t;
}

async function createApprovedInvoice(
	page: import('@playwright/test').Page,
	invoiceNumber: string
): Promise<string> {
	const token = await authToken(page);
	const resp = await page.request.post(`${API_BASE}/api/invoices`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
		data: {
			vendor: 'E2E Pluralization Vendor',
			invoice_number: invoiceNumber,
			amount: 250.0,
			currency: 'USD',
			status: 'approved'
		}
	});
	expect(resp.status()).toBe(201);
	const body = (await resp.json()) as { id: string };
	return body.id;
}

/** Wipe an approved test invoice + its workflow rows + audit trail.
 *  The PATCH/DELETE invoice endpoint won't touch approved rows, so
 *  raw SQL is the only revertible path. Same psql shape used by
 *  workflows/invoice-routing.spec.ts. */
function hardDeleteInvoice(id: string): void {
	execFileSync(
		'psql',
		[
			'-h', 'localhost',
			'-U', 'postgres',
			'-p', '5432',
			'-d', 'ap_acme',
			'-c', `DELETE FROM workflow_steps WHERE instance_id IN (SELECT id FROM workflow_instances WHERE invoice_id='${id}')`,
			'-c', `DELETE FROM workflow_instances WHERE invoice_id='${id}'`,
			'-c', `DELETE FROM audit_log WHERE entity_id='${id}'`,
			'-c', `DELETE FROM invoices WHERE id='${id}'`
		],
		{ env: { ...process.env, PGPASSWORD: 'postgres' }, stdio: 'pipe' }
	);
}

/**
 * /payments Queue tab — selection, Review & Pay panel, payment-method
 * selector, and Clear. Stops short of clicking "Create Draft Run" so
 * we don't pollute the seed payment_runs table; that path is exercised
 * by the runs detail tests via the API.
 */

test.describe('/payments queue selection (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
		await page.goto('/payments');
		await page.waitForLoadState('networkidle');
		// Queue is the default tab, but be defensive in case a prior
		// test left a different tab active in shared state.
		await page.locator('.tab', { hasText: 'Queue' }).click();
	});

	test('queue table renders rows from /api/payments/queue', async ({ page }) => {
		// The seeded `approved` invoices should be ready to pay. If the
		// seed has zero queue items, the rest of the suite is moot, so
		// fail fast with a helpful message.
		const rows = page.locator('table tbody tr');
		await expect(rows.first()).toBeVisible({ timeout: 5_000 });
		expect(await rows.count()).toBeGreaterThan(0);
		// Header includes the checkbox column.
		await expect(page.locator('thead th.checkbox-col input[type="checkbox"]')).toBeVisible();
	});

	test('selecting a row reveals the pay-bar with count + total', async ({ page }) => {
		const firstRow = page.locator('table tbody tr').first();
		await firstRow.locator('input[type="checkbox"]').check();

		const payBar = page.locator('.pay-bar');
		await expect(payBar).toBeVisible();
		await expect(payBar.locator('.pay-bar-count')).toContainText('1 selected');
		// Pay-bar shows a currency-formatted total (USD format includes "$").
		await expect(payBar.locator('.pay-bar-count')).toContainText('$');
	});

	test('selecting all via header checkbox selects every queue row', async ({ page }) => {
		const total = await page.locator('table tbody tr').count();
		await page.locator('thead th.checkbox-col input[type="checkbox"]').check();

		const payBar = page.locator('.pay-bar');
		await expect(payBar).toBeVisible();
		await expect(payBar.locator('.pay-bar-count')).toContainText(`${total} selected`);
		// Every body checkbox is now checked.
		const bodyChecks = page.locator('table tbody tr input[type="checkbox"]');
		const checkCount = await bodyChecks.count();
		for (let i = 0; i < checkCount; i++) {
			await expect(bodyChecks.nth(i)).toBeChecked();
		}
	});

	test('Clear button drops selection and hides pay-bar', async ({ page }) => {
		await page.locator('table tbody tr').first().locator('input[type="checkbox"]').check();
		const payBar = page.locator('.pay-bar');
		await expect(payBar).toBeVisible();

		await payBar.getByRole('button', { name: 'Clear' }).click();
		await expect(payBar).toBeHidden();
		await expect(
			page.locator('table tbody tr').first().locator('input[type="checkbox"]')
		).not.toBeChecked();
	});

	test('Review & Pay opens the panel; method selects default to ACH', async ({ page }) => {
		await page.locator('table tbody tr').first().locator('input[type="checkbox"]').check();
		await page.locator('.pay-bar').getByRole('button', { name: 'Review & Pay' }).click();

		const reviewPanel = page.locator('.review-panel');
		await expect(reviewPanel).toBeVisible();
		await expect(reviewPanel.locator('.review-title')).toContainText('Payment Review');

		// One row per selected invoice; the method <select> defaults to "ach".
		const methodSelect = reviewPanel.locator('select.method-select').first();
		await expect(methodSelect).toBeVisible();
		await expect(methodSelect).toHaveValue('ach');

		// All four options exist (ach, wire, check, virtual_card).
		const optionValues = await methodSelect.locator('option').evaluateAll(
			(opts) => (opts as HTMLOptionElement[]).map((o) => o.value)
		);
		expect(optionValues).toEqual(['ach', 'wire', 'check', 'virtual_card']);
	});

	test('changing the payment-method select sticks while panel is open', async ({ page }) => {
		await page.locator('table tbody tr').first().locator('input[type="checkbox"]').check();
		await page.locator('.pay-bar').getByRole('button', { name: 'Review & Pay' }).click();

		const select = page.locator('.review-panel select.method-select').first();
		await select.selectOption('wire');
		await expect(select).toHaveValue('wire');
		// Switching it again confirms the binding isn't one-way.
		await select.selectOption('check');
		await expect(select).toHaveValue('check');
	});

	test('Review panel button label reflects selection size pluralization', async ({
		page
	}) => {
		// Seed produces only one approved invoice that's still in the
		// queue (the other approved rows already have payment records).
		// Mint two throwaway approved invoices so we can assert "2
		// Invoices" pluralization, then hard-delete via psql.
		const stamp = Date.now();
		const created: string[] = [];
		try {
			created.push(await createApprovedInvoice(page, `E2E-PAY-${stamp}-A`));
			created.push(await createApprovedInvoice(page, `E2E-PAY-${stamp}-B`));

			await page.reload();
			await page.waitForLoadState('networkidle');
			await page.locator('.tab', { hasText: 'Queue' }).click();

			const rowA = page.locator('table tbody tr', { hasText: `E2E-PAY-${stamp}-A` });
			const rowB = page.locator('table tbody tr', { hasText: `E2E-PAY-${stamp}-B` });
			await expect(rowA).toBeVisible();
			await expect(rowB).toBeVisible();

			await rowA.locator('input[type="checkbox"]').check();
			await rowB.locator('input[type="checkbox"]').check();
			await page.locator('.pay-bar').getByRole('button', { name: 'Review & Pay' }).click();

			const executeBtn = page.locator('.review-panel .btn-execute');
			await expect(executeBtn).toContainText('2 Invoices');
		} finally {
			for (const id of created) hardDeleteInvoice(id);
		}
	});
});
