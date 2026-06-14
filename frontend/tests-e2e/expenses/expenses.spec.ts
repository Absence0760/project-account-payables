import { API_BASE, authedTenantHeaders, expect, tenantPsql, test } from '../fixtures/helpers';

interface GlAccount {
	id: string;
	code: string;
	name: string;
}

async function getFirstGlAccount(
	page: import('@playwright/test').Page
): Promise<GlAccount | null> {
	const resp = await page.request.get(`${API_BASE}/api/gl-accounts`, {
		headers: await authedTenantHeaders(page)
	});
	const body = (await resp.json()) as GlAccount[];
	return body.length > 0 ? body[0] : null;
}

async function createExpense(
	page: import('@playwright/test').Page,
	data: Record<string, unknown>
): Promise<{ id: string; status: string; merchant: string | null }> {
	const resp = await page.request.post(`${API_BASE}/api/expenses`, {
		headers: await authedTenantHeaders(page),
		data
	});
	expect(resp.status()).toBe(201);
	return (await resp.json()) as { id: string; status: string; merchant: string | null };
}

async function createReport(
	page: import('@playwright/test').Page,
	data: Record<string, unknown>
): Promise<{ id: string; report_number: string; status: string }> {
	const resp = await page.request.post(`${API_BASE}/api/expense-reports`, {
		headers: await authedTenantHeaders(page),
		data
	});
	expect(resp.status()).toBe(201);
	return (await resp.json()) as { id: string; report_number: string; status: string };
}

/** Hard-delete an expense via psql (cleanup). Detach from any report first so
 *  the FK doesn't block — but since we null report_id on the row itself the
 *  delete is safe. */
function deleteExpense(id: string): void {
	tenantPsql(`DELETE FROM expenses WHERE id='${id}'`);
}

function deleteReport(id: string): void {
	// Null out any expense still pointing at the report, then delete it.
	tenantPsql(`UPDATE expenses SET report_id=NULL WHERE report_id='${id}'`);
	tenantPsql(`DELETE FROM expense_reports WHERE id='${id}'`);
}

test.describe('/expenses', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/expenses');
		await page.waitForLoadState('networkidle');
	});

	test('renders the expenses workspace', async ({ page }) => {
		await expect(page.getByRole('heading', { name: 'Expenses' })).toBeVisible();
		await expect(page.locator('table')).toBeVisible();
	});

	test('a created expense appears in the list and updates KPIs', async ({ page }) => {
		const merchant = `E2E Coffee ${Date.now()}`;
		let id: string | null = null;
		try {
			const created = await createExpense(page, {
				merchant,
				amount: '12.50',
				currency: 'USD',
				expense_date: '2026-01-01',
				category: 'meals'
			});
			id = created.id;
			expect(created.status).toBe('draft');

			await page.goto(`/expenses?search=${encodeURIComponent(merchant)}`);
			await page.waitForLoadState('networkidle');
			await expect(page.getByText(merchant)).toBeVisible();

			// KPI cards render (period total + count are present on the expenses tab).
			await expect(page.locator('.kpi')).not.toHaveCount(0);
		} finally {
			if (id) deleteExpense(id);
		}
	});

	test('bulk GL-codes a selected expense', async ({ page }) => {
		const gl = await getFirstGlAccount(page);
		test.skip(gl === null, 'no GL accounts seeded in this tenant');
		const merchant = `E2E GL ${Date.now()}`;
		let id: string | null = null;
		try {
			const created = await createExpense(page, {
				merchant,
				amount: '40.00',
				currency: 'USD',
				expense_date: '2026-01-02',
				category: 'travel'
			});
			id = created.id;

			// Drive the bulk-gl-code endpoint directly (the API the BulkBar calls).
			const resp = await page.request.post(`${API_BASE}/api/expenses/bulk-gl-code`, {
				headers: await authedTenantHeaders(page),
				data: { expense_ids: [id], gl_account_id: gl!.id }
			});
			expect(resp.status()).toBe(200);
			expect(((await resp.json()) as { updated: number }).updated).toBe(1);

			// The GL code is now visible in the row's GL cell after a reload.
			await page.goto(`/expenses?search=${encodeURIComponent(merchant)}`);
			await page.waitForLoadState('networkidle');
			await expect(page.getByText(merchant)).toBeVisible();
			await expect(page.getByText(gl!.code, { exact: false }).first()).toBeVisible();
		} finally {
			if (id) deleteExpense(id);
		}
	});

	test('builds a report, attaches an expense, and shows the summary', async ({ page }) => {
		const merchant = `E2E Report Item ${Date.now()}`;
		const reportNumber = `E2E-RPT-${Date.now()}`;
		let expenseId: string | null = null;
		let reportId: string | null = null;
		try {
			const exp = await createExpense(page, {
				merchant,
				amount: '99.00',
				currency: 'USD',
				expense_date: '2026-01-03',
				category: 'supplies'
			});
			expenseId = exp.id;

			const report = await createReport(page, {
				report_number: reportNumber,
				title: 'E2E trip',
				currency: 'USD'
			});
			reportId = report.id;

			// Attach the expense to the report.
			const attach = await page.request.post(
				`${API_BASE}/api/expense-reports/${reportId}/expenses`,
				{
					headers: await authedTenantHeaders(page),
					data: { expense_ids: [expenseId], detach: false }
				}
			);
			expect(attach.status()).toBe(200);

			// The summary reflects the attached expense.
			const summary = await page.request.get(
				`${API_BASE}/api/expense-reports/${reportId}/summary`,
				{ headers: await authedTenantHeaders(page) }
			);
			expect(summary.status()).toBe(200);
			const sBody = (await summary.json()) as { total: number; count: number };
			expect(sBody.count).toBe(1);
			expect(sBody.total).toBeCloseTo(99.0, 2);

			// The Reports tab lists the report and opening it shows the row.
			await page.goto('/expenses?tab=reports');
			await page.waitForLoadState('networkidle');
			await expect(page.getByText(reportNumber)).toBeVisible();
			await page.getByRole('button', { name: `Open report ${reportNumber}` }).click();
			await expect(page.getByText(merchant)).toBeVisible();
		} finally {
			if (reportId) deleteReport(reportId);
			if (expenseId) deleteExpense(expenseId);
		}
	});

	test('exports the expense register as CSV', async ({ page }) => {
		const exp = await page.request.get(`${API_BASE}/api/expenses/export`, {
			headers: await authedTenantHeaders(page)
		});
		expect(exp.status()).toBe(200);
		expect(exp.headers()['content-type']).toContain('text/csv');
		expect(exp.headers()['content-disposition']).toContain('expenses_');
	});
});
