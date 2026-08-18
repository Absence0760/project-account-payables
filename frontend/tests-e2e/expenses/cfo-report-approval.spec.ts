import {
	API_BASE,
	currentTenantSlug,
	expect,
	tenantHeaders,
	tenantPsql,
	test
} from '../fixtures/helpers';

/**
 * /expenses → Reports — the CFO can approve.
 *
 * Regression: the page gated BOTH Approve and Reject on one `canDecideReport`
 * predicate built from `auth.isManager` (= admin | ap_manager). The backend
 * authorises approve for admin | ap_manager | **cfo**
 * (`backend/app/api/expenses.py`), and above
 * `settings.expense_approval.cfo_threshold` (default 5000) it REQUIRES cfo or
 * admin. So an over-threshold report 403'd for the ap_manager and showed the CFO
 * no Approve button at all — the exact person the gate escalates to could not
 * act, from the UI, ever. Reject genuinely is admin | ap_manager, so the shared
 * predicate was right for Reject and wrong for Approve; they are now split.
 *
 * Setup mirrors `report-approval-controls.spec.ts`: per-role JWTs minted once
 * via the API (the login route is rate-limited 10/60s per IP) and injected into
 * localStorage for the UI leg. Reports are built under the CLERK so the CFO is
 * never the submitter — otherwise segregation of duties fires instead.
 */

interface Created {
	id: string;
}
type ReportRow = { id: string; status: string; report_number: string };

const _tokenCache = new Map<string, string>();

async function roleToken(
	page: import('@playwright/test').Page,
	creds: { email: string; password: string }
): Promise<string> {
	const slug = currentTenantSlug();
	const key = `${slug}:${creds.email}`;
	let token = _tokenCache.get(key);
	if (!token) {
		const res = await page.request.post(`${API_BASE}/api/auth/login`, {
			headers: { 'X-Tenant-Slug': slug },
			data: { email: creds.email, password: creds.password }
		});
		expect(res.status(), 'login should not be rate-limited (cached per role)').toBe(200);
		const body = (await res.json()) as { access_token?: string; token?: string };
		token = body.access_token ?? body.token;
		expect(token, 'login returned a token').toBeTruthy();
		_tokenCache.set(key, token as string);
	}
	return token as string;
}

async function roleHeaders(
	page: import('@playwright/test').Page,
	creds: { email: string; password: string }
): Promise<Record<string, string>> {
	return tenantHeaders(await roleToken(page, creds), currentTenantSlug());
}

async function actAsInUi(
	page: import('@playwright/test').Page,
	creds: { email: string; password: string }
): Promise<void> {
	const token = await roleToken(page, creds);
	await page.evaluate((t) => localStorage.setItem('auth_token', t), token);
	await page.goto('/expenses?tab=reports');
	await page.waitForLoadState('networkidle');
}

function deleteReport(id: string): void {
	tenantPsql(`UPDATE expenses SET report_id=NULL WHERE report_id='${id}'`);
	tenantPsql(`DELETE FROM expense_reports WHERE id='${id}'`);
}
function deleteExpense(id: string): void {
	tenantPsql(`DELETE FROM expenses WHERE id='${id}'`);
}

/** A clerk-owned, submitted report holding one expense of `amount`. */
async function buildSubmittedReport(
	page: import('@playwright/test').Page,
	clerkH: Record<string, string>,
	amount: string,
	tag: string
): Promise<{ reportId: string; expenseId: string; reportNumber: string }> {
	const reportNumber = `E2E-CFO-${tag}-${Date.now()}`;
	const expResp = await page.request.post(`${API_BASE}/api/expenses`, {
		headers: clerkH,
		data: {
			merchant: `E2E CFO ${tag} ${Date.now()}`,
			amount,
			currency: 'USD',
			expense_date: '2026-03-01',
			category: 'travel'
		}
	});
	expect(expResp.status()).toBe(201);
	const expenseId = ((await expResp.json()) as Created).id;

	const rptResp = await page.request.post(`${API_BASE}/api/expense-reports`, {
		headers: clerkH,
		data: { report_number: reportNumber, title: `CFO approve ${tag}`, currency: 'USD' }
	});
	expect(rptResp.status()).toBe(201);
	const reportId = ((await rptResp.json()) as Created).id;

	expect(
		(
			await page.request.post(`${API_BASE}/api/expense-reports/${reportId}/expenses`, {
				headers: clerkH,
				data: { expense_ids: [expenseId], detach: false }
			})
		).status()
	).toBe(200);

	const submit = await page.request.post(`${API_BASE}/api/expense-reports/${reportId}/submit`, {
		headers: clerkH,
		data: {}
	});
	expect(submit.status()).toBe(200);
	expect(((await submit.json()) as ReportRow).status).toBe('submitted');
	return { reportId, expenseId, reportNumber };
}

test.describe('/expenses — CFO report approval', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/expenses');
		await page.waitForLoadState('networkidle');
	});

	test('a CFO sees Approve on an OVER-threshold report and it works', async ({
		page,
		tenantCfo,
		tenantClerk
	}) => {
		let reportId: string | null = null;
		let expenseId: string | null = null;
		try {
			// 7500 > the platform default 5000 threshold — the case that demands
			// cfo/admin server-side, and the case the CFO could not act on at all.
			const built = await buildSubmittedReport(
				page,
				await roleHeaders(page, tenantClerk),
				'7500.00',
				'OVER'
			);
			reportId = built.reportId;
			expenseId = built.expenseId;

			await actAsInUi(page, tenantCfo);
			await page.getByRole('button', { name: `Open report ${built.reportNumber}` }).click();
			await expect(page.locator('.report-title-block .badge')).toHaveText('Submitted');

			const approve = page.getByRole('button', { name: 'Approve', exact: true });
			await expect(approve, 'the CFO must be offered Approve').toBeVisible();
			await approve.click();

			await expect(page.locator('.report-title-block .badge')).toHaveText('Approved');
			expect(
				tenantPsql(`SELECT status FROM expense_reports WHERE id='${reportId}'`).trim()
			).toBe('approved');
		} finally {
			if (reportId) deleteReport(reportId);
			if (expenseId) deleteExpense(expenseId);
		}
	});

	test('a CFO is NOT offered Reject — that stays admin | ap_manager', async ({
		page,
		tenantCfo,
		tenantClerk
	}) => {
		let reportId: string | null = null;
		let expenseId: string | null = null;
		try {
			const built = await buildSubmittedReport(
				page,
				await roleHeaders(page, tenantClerk),
				'120.00',
				'REJ'
			);
			reportId = built.reportId;
			expenseId = built.expenseId;

			await actAsInUi(page, tenantCfo);
			await page.getByRole('button', { name: `Open report ${built.reportNumber}` }).click();
			await expect(page.locator('.report-title-block .badge')).toHaveText('Submitted');

			// Approve is offered (wider role set); Reject is not — the split is the
			// whole point, so widening BOTH would be the wrong fix.
			await expect(page.getByRole('button', { name: 'Approve', exact: true })).toBeVisible();
			await expect(page.getByRole('button', { name: 'Reject', exact: true })).toHaveCount(0);
		} finally {
			if (reportId) deleteReport(reportId);
			if (expenseId) deleteExpense(expenseId);
		}
	});

	test('a manager still sees both Approve and Reject', async ({
		page,
		tenantManager,
		tenantClerk
	}) => {
		let reportId: string | null = null;
		let expenseId: string | null = null;
		try {
			const built = await buildSubmittedReport(
				page,
				await roleHeaders(page, tenantClerk),
				'80.00',
				'MGR'
			);
			reportId = built.reportId;
			expenseId = built.expenseId;

			await actAsInUi(page, tenantManager);
			await page.getByRole('button', { name: `Open report ${built.reportNumber}` }).click();
			await expect(page.getByRole('button', { name: 'Approve', exact: true })).toBeVisible();
			await expect(page.getByRole('button', { name: 'Reject', exact: true })).toBeVisible();
		} finally {
			if (reportId) deleteReport(reportId);
			if (expenseId) deleteExpense(expenseId);
		}
	});
});
