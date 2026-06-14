import {
	API_BASE,
	authedTenantHeaders,
	expect,
	signInAndWait,
	tenantPsql,
	test
} from '../fixtures/helpers';

// WF3 — expense policies, blocking-submit, manager approval (segregation).
//
// The worker default-storageState is the tenant ADMIN. Admin is a manager
// (so it can approve/reject) but is ALSO the report owner when a report is
// created with the admin token — which would fail the approver != submitter
// segregation rule. So the happy path creates the report under the CLERK
// (a non-manager), then approves as the ADMIN: approver (admin) != owner
// (clerk) → segregation passes.

interface Created {
	id: string;
}

async function clerkHeaders(
	page: import('@playwright/test').Page,
	creds: { email: string; password: string }
): Promise<Record<string, string>> {
	await signInAndWait(page, creds);
	return authedTenantHeaders(page);
}

function deletePolicy(id: string): void {
	tenantPsql(`DELETE FROM expense_policies WHERE id='${id}'`);
}

function deleteReport(id: string): void {
	tenantPsql(`UPDATE expenses SET report_id=NULL WHERE report_id='${id}'`);
	tenantPsql(`DELETE FROM expense_reports WHERE id='${id}'`);
}

function deleteExpense(id: string): void {
	tenantPsql(`DELETE FROM expenses WHERE id='${id}'`);
}

function deletePreapproval(id: string): void {
	tenantPsql(`DELETE FROM expense_preapprovals WHERE id='${id}'`);
}

test.describe('/expenses — WF3 approval', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/expenses');
		await page.waitForLoadState('networkidle');
	});

	test('a policy-violating expense shows a violation badge', async ({ page }) => {
		const merchant = `E2E Violation ${Date.now()}`;
		let policyId: string | null = null;
		let expenseId: string | null = null;
		try {
			// A policy that caps meals at $10 and requires a receipt over $5.
			const polResp = await page.request.post(`${API_BASE}/api/expense-policies`, {
				headers: await authedTenantHeaders(page),
				data: {
					name: `E2E Meals Cap ${Date.now()}`,
					active: true,
					category: 'meals',
					category_limit: '10.00',
					requires_receipt_above: '5.00'
				}
			});
			expect(polResp.status()).toBe(201);
			policyId = ((await polResp.json()) as Created).id;

			// A $50 meals expense with no receipt — violates both rules.
			const expResp = await page.request.post(`${API_BASE}/api/expenses`, {
				headers: await authedTenantHeaders(page),
				data: { merchant, amount: '50.00', currency: 'USD', expense_date: '2026-02-01', category: 'meals' }
			});
			expect(expResp.status()).toBe(201);
			expenseId = ((await expResp.json()) as Created).id;

			await page.goto(`/expenses?search=${encodeURIComponent(merchant)}`);
			await page.waitForLoadState('networkidle');
			await expect(page.getByText(merchant)).toBeVisible();
			// The ⚠ violation pill renders next to the status badge.
			await expect(page.locator('.badge.violation').first()).toBeVisible();
		} finally {
			if (expenseId) deleteExpense(expenseId);
			if (policyId) deletePolicy(policyId);
		}
	});

	test('admin can create + delete a policy through the UI', async ({ page }) => {
		const name = `E2E Policy UI ${Date.now()}`;
		let policyId: string | null = null;
		try {
			await page.goto('/expenses?tab=policies');
			await page.waitForLoadState('networkidle');
			await page.getByRole('button', { name: '+ New Policy' }).click();

			const dialog = page.getByRole('dialog', { name: 'New policy' });
			await expect(dialog).toBeVisible();
			// Name is the first text input in the policy form.
			await dialog.locator('input[type="text"]').first().fill(name);
			await dialog.getByRole('button', { name: 'Create' }).click();

			await expect(page.getByText(name)).toBeVisible();

			// Capture the id for cleanup (UI gives no id; read it back via the API).
			const list = await page.request.get(`${API_BASE}/api/expense-policies`, {
				headers: await authedTenantHeaders(page)
			});
			const policies = (await list.json()) as { id: string; name: string }[];
			policyId = policies.find((p) => p.name === name)?.id ?? null;
			expect(policyId).not.toBeNull();
		} finally {
			if (policyId) deletePolicy(policyId);
		}
	});

	test('submit blocks on a blocking violation, then a different manager approves', async ({
		page,
		tenantAdmin,
		tenantClerk
	}) => {
		const merchant = `E2E Approve Flow ${Date.now()}`;
		const reportNumber = `E2E-WF3-${Date.now()}`;
		let policyId: string | null = null;
		let expenseId: string | null = null;
		let reportId: string | null = null;
		try {
			// Policy: meals require a receipt above $5 (a blocking violation for a
			// $50 receiptless expense → submit must 422).
			const polResp = await page.request.post(`${API_BASE}/api/expense-policies`, {
				headers: await authedTenantHeaders(page),
				data: {
					name: `E2E Receipt Rule ${Date.now()}`,
					active: true,
					category: 'meals',
					requires_receipt_above: '5.00'
				}
			});
			expect(polResp.status()).toBe(201);
			policyId = ((await polResp.json()) as Created).id;

			// Build the report + expense as the CLERK so the report owner
			// (employee_user_id) is the clerk, not the approving admin.
			const clerkH = await clerkHeaders(page, tenantClerk);

			const expResp = await page.request.post(`${API_BASE}/api/expenses`, {
				headers: clerkH,
				data: { merchant, amount: '50.00', currency: 'USD', expense_date: '2026-02-02', category: 'meals' }
			});
			expect(expResp.status()).toBe(201);
			expenseId = ((await expResp.json()) as Created).id;

			const rptResp = await page.request.post(`${API_BASE}/api/expense-reports`, {
				headers: clerkH,
				data: { report_number: reportNumber, title: 'WF3 trip', currency: 'USD' }
			});
			expect(rptResp.status()).toBe(201);
			reportId = ((await rptResp.json()) as Created).id;

			const attach = await page.request.post(
				`${API_BASE}/api/expense-reports/${reportId}/expenses`,
				{ headers: clerkH, data: { expense_ids: [expenseId], detach: false } }
			);
			expect(attach.status()).toBe(200);

			// The clerk owns the report — open it and try to submit. The missing
			// receipt is a blocking violation, so the UI surfaces the panel/toast
			// and the report stays in draft (no transition).
			await page.goto('/expenses?tab=reports');
			await page.waitForLoadState('networkidle');
			await page.getByRole('button', { name: `Open report ${reportNumber}` }).click();
			await page.getByRole('button', { name: 'Submit' }).click();
			// Inline violation panel renders the receipt rule.
			await expect(page.getByText(/receipt/i)).toBeVisible();
			// Still draft (the submit was rejected).
			await expect(page.locator('.report-title-block .badge')).toHaveText('Draft');

			// Resolve the violation at the data layer (stamp a receipt key + clear
			// the cached violations) so the re-submit can pass — the policy engine
			// re-evaluates on the submit. This mirrors uploading a receipt.
			tenantPsql(
				`UPDATE expenses SET receipt_file_key='e2e/receipt.pdf', policy_violations='[]'::jsonb WHERE id='${expenseId}'`
			);

			// Re-submit (still the clerk owner). Now it transitions to submitted.
			await page.reload();
			await page.waitForLoadState('networkidle');
			await page.getByRole('button', { name: `Open report ${reportNumber}` }).click();
			await page.getByRole('button', { name: 'Submit' }).click();
			await expect(page.locator('.report-title-block .badge')).toHaveText('Submitted');

			// API-level segregation check: the OWNER (clerk) cannot approve.
			const selfApprove = await page.request.post(
				`${API_BASE}/api/expense-reports/${reportId}/approve`,
				{ headers: clerkH, data: {} }
			);
			expect(selfApprove.status()).toBe(403);

			// Approve as the ADMIN (a manager, and NOT the owner) through the UI.
			await signInAndWait(page, tenantAdmin);
			await page.goto('/expenses?tab=reports');
			await page.waitForLoadState('networkidle');
			await page.getByRole('button', { name: `Open report ${reportNumber}` }).click();
			await page.getByRole('button', { name: 'Approve' }).click();
			await expect(page.locator('.report-title-block .badge')).toHaveText('Approved');
		} finally {
			if (reportId) deleteReport(reportId);
			if (expenseId) deleteExpense(expenseId);
			if (policyId) deletePolicy(policyId);
		}
	});

	test('a manager approves a pre-approval request from a different requester', async ({
		page,
		tenantAdmin,
		tenantClerk
	}) => {
		const title = `E2E Preapproval ${Date.now()}`;
		let paId: string | null = null;
		try {
			// Clerk raises the request (requester = clerk).
			const clerkH = await clerkHeaders(page, tenantClerk);
			const paResp = await page.request.post(`${API_BASE}/api/expense-preapprovals`, {
				headers: clerkH,
				data: { title, estimated_amount: '1200.00', currency: 'USD', category: 'travel' }
			});
			expect(paResp.status()).toBe(201);
			paId = ((await paResp.json()) as Created).id;

			// Admin (manager, different user) approves it in the Pre-approvals tab.
			await signInAndWait(page, tenantAdmin);
			await page.goto('/expenses?tab=preapprovals');
			await page.waitForLoadState('networkidle');
			await expect(page.getByText(title)).toBeVisible();
			await page.getByRole('button', { name: 'Approve' }).click();
			await expect(page.locator('.badge.approved').first()).toBeVisible();
		} finally {
			if (paId) deletePreapproval(paId);
		}
	});
});
