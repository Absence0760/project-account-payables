import {
	API_BASE,
	currentTenantSlug,
	expect,
	signInAndWait,
	signOut,
	tenantHeaders,
	tenantPsql,
	test
} from '../fixtures/helpers';

/**
 * `InvoiceModal` — multi-level approval-chain progress stepper.
 *
 * `GET /api/invoices/{id}/workflow` exposes the chain's live state on
 * `state_data.approval_levels` (`backend/app/services/approval_chain.py`),
 * but nothing rendered it and every partial approval showed the identical
 * "approved" toast as a final one — an approver who thought their sign-off
 * just sent the invoice onward, when a second level still needed to act, had
 * no way to tell. This spec drives a real 2-level chain end to end: after the
 * first approval the modal must show the `ApprovalChainProgress` stepper
 * (level 1 done, level 2 in progress) and the partial-approval toast; after
 * the second, the final-approval toast and no more review actions.
 *
 * Each level needs a DIFFERENT approver — `advance_approval_chain` refuses
 * the same actor on two levels of one chain ("You already approved an
 * earlier level of this chain; a different approver is required"), so this
 * spec uses three distinct seeded users: manager uploads (and can never
 * approve — `require_segregation`), admin approves level 1, cfo approves
 * level 2.
 */

interface WorkflowResponse {
	id: string;
	is_active: boolean;
	steps_config: { steps: Array<{ type: string; enabled: boolean; config: Record<string, unknown> }> };
}

type Creds = { email: string; password: string };

async function loginHeaders(creds: Creds): Promise<Record<string, string>> {
	const slug = currentTenantSlug();
	const resp = await fetch(`${API_BASE}/api/auth/login`, {
		method: 'POST',
		headers: { 'X-Tenant-Slug': slug, 'Content-Type': 'application/json' },
		body: JSON.stringify(creds)
	});
	expect(resp.ok, `login failed for ${creds.email} (${resp.status})`).toBe(true);
	const { access_token } = (await resp.json()) as { access_token: string };
	return tenantHeaders(access_token, slug);
}

async function listWorkflows(headers: Record<string, string>) {
	const resp = await fetch(`${API_BASE}/api/workflows`, { headers });
	return ((await resp.json()) as { items: WorkflowResponse[] }).items;
}

async function patchWorkflow(headers: Record<string, string>, id: string, body: Record<string, unknown>) {
	return fetch(`${API_BASE}/api/workflows/${id}`, {
		method: 'PATCH',
		headers: { ...headers, 'Content-Type': 'application/json' },
		body: JSON.stringify(body)
	});
}

async function createWorkflow(
	headers: Record<string, string>,
	name: string,
	steps: Array<{ type: string; enabled: boolean; config: Record<string, unknown> }>
): Promise<string> {
	const resp = await fetch(`${API_BASE}/api/workflows`, {
		method: 'POST',
		headers: { ...headers, 'Content-Type': 'application/json' },
		body: JSON.stringify({ name, steps: steps.map((s, i) => ({ number: i + 1, name: s.type, ...s })) })
	});
	expect(resp.ok, `create workflow failed (${resp.status})`).toBe(true);
	return ((await resp.json()) as { id: string }).id;
}

async function createInvoice(
	headers: Record<string, string>,
	invoiceNumber: string
): Promise<string> {
	const resp = await fetch(`${API_BASE}/api/invoices`, {
		method: 'POST',
		headers: { ...headers, 'Content-Type': 'application/json' },
		body: JSON.stringify({
			vendor: 'E2E Approval Chain Vendor',
			invoice_number: invoiceNumber,
			amount: '250.00',
			currency: 'USD'
		})
	});
	expect(resp.ok, `create invoice failed (${resp.status})`).toBe(true);
	return ((await resp.json()) as { id: string }).id;
}

async function completeInvoice(headers: Record<string, string>, id: string) {
	const resp = await fetch(`${API_BASE}/api/invoices/${id}/complete`, { method: 'POST', headers });
	expect(resp.ok, `complete invoice failed (${resp.status})`).toBe(true);
	return ((await resp.json()) as { status: string }).status;
}

function cleanUp(id: string | null) {
	if (!id) return;
	tenantPsql(
		`DELETE FROM workflow_steps WHERE instance_id IN (SELECT id FROM workflow_instances WHERE invoice_id='${id}')`
	);
	tenantPsql(`DELETE FROM workflow_instances WHERE invoice_id='${id}'`);
	tenantPsql(`DELETE FROM exceptions WHERE invoice_id='${id}'`);
	tenantPsql(`DELETE FROM invoices WHERE id='${id}'`);
}

/** Search the list down to one invoice number and open its detail modal. */
async function openInvoice(page: import('@playwright/test').Page, invoiceNumber: string) {
	await page.goto('/invoices');
	const listed = page.waitForResponse(
		(r) =>
			r.url().includes('/api/invoices?') &&
			r.url().includes(`search=${encodeURIComponent(invoiceNumber)}`) &&
			r.request().method() === 'GET'
	);
	await page.getByPlaceholder('Search invoices...').fill(invoiceNumber);
	await listed;
	const row = page.locator('table tbody tr', { hasText: invoiceNumber }).first();
	await expect(row).toBeVisible();
	await row.click();
}

const CHAIN_LEVEL: Record<string, unknown> = {
	min_amount: null,
	max_amount: null,
	approver_ids: [],
	required_approvals: 1,
	parallel_mode: 'any',
	routing_rules: [],
	escalation_hours: null,
	escalation_to_user_ids: []
};

test.describe('/invoices approval-chain progress stepper', () => {
	let seedActiveId: string | null = null;
	let chainWorkflowId: string | null = null;
	let invoiceId: string | null = null;

	test.afterEach(async ({ tenantAdmin }) => {
		cleanUp(invoiceId);
		invoiceId = null;
		if (chainWorkflowId) {
			// A fresh admin login, independent of whatever the page's own
			// session ended the test as (this spec signs in as cfo partway
			// through) — workflow-definition mutation is admin-only.
			const adminHeaders = await loginHeaders(tenantAdmin);
			await patchWorkflow(adminHeaders, chainWorkflowId, { is_active: false });
			if (seedActiveId) {
				await patchWorkflow(adminHeaders, seedActiveId, { is_active: true });
			}
			await fetch(`${API_BASE}/api/workflows/${chainWorkflowId}`, {
				method: 'DELETE',
				headers: adminHeaders
			});
			chainWorkflowId = null;
		}
	});

	test('a 2-level chain shows partial progress after level 1, then the final toast after level 2', async ({
		page,
		tenantAdmin,
		tenantManager,
		tenantCfo
	}) => {
		const adminHeaders = await loginHeaders(tenantAdmin);
		const wfsBefore = await listWorkflows(adminHeaders);
		const seedActive = wfsBefore.find((w) => w.is_active) ?? null;
		expect(seedActive, 'seeded tenant must have an active workflow').not.toBeNull();
		seedActiveId = seedActive!.id;

		chainWorkflowId = await createWorkflow(adminHeaders, `e2e-chain-progress-${Date.now()}`, [
			{
				type: 'extraction',
				enabled: true,
				config: { auto_approve_enabled: false, auto_approve_threshold: 0.95 }
			},
			{
				type: 'approval',
				enabled: true,
				config: {
					required: true,
					auto_approve_below: null,
					approver_strategy: 'chain',
					approver_ids: [],
					require_segregation: true,
					approval_chain: [
						{ ...CHAIN_LEVEL, name: 'Level 1' },
						{ ...CHAIN_LEVEL, name: 'Level 2' }
					]
				}
			},
			// Disabled — this spec cares about the chain progress UI, not the
			// ERP dispatch that would otherwise fire the moment the second
			// approval lands.
			{ type: 'erp_export', enabled: false, config: {} }
		]);
		await patchWorkflow(adminHeaders, chainWorkflowId, { is_active: true });

		const managerHeaders = await loginHeaders(tenantManager);
		const invoiceNumber = `CHAIN-${Date.now()}`;
		invoiceId = await createInvoice(managerHeaders, invoiceNumber);
		const afterComplete = await completeInvoice(adminHeaders, invoiceId);
		expect(afterComplete).toBe('ready_for_review');

		// First approval (admin) — the chain is not yet satisfied, so the
		// invoice stays in ready_for_review and the toast must say so, not
		// claim success. `page` is already signed in as admin (worker
		// storageState default).
		await openInvoice(page, invoiceNumber);
		await expect(page.getByRole('button', { name: 'Approve', exact: true })).toBeVisible();
		await page.getByRole('button', { name: 'Approve', exact: true }).click();
		await expect(
			page.getByText('1 more approval is needed before it moves forward.', { exact: false })
		).toBeVisible();

		// Reopen — the modal reloads the workflow instance on mount, so the
		// stepper must now render level 1 done and level 2 in progress.
		await openInvoice(page, invoiceNumber);
		const chain = page.locator('[data-testid="approval-chain-progress"]');
		await expect(chain).toBeVisible();
		await expect(chain.getByText('Approval progress')).toBeVisible();
		const levelItems = chain.locator('.chain-level');
		await expect(levelItems).toHaveCount(2);
		await expect(levelItems.nth(0)).toHaveClass(/chain-level-done/);
		await expect(levelItems.nth(0).getByText('Approved', { exact: true })).toBeVisible();
		await expect(levelItems.nth(1)).toHaveClass(/chain-level-current/);
		await expect(levelItems.nth(1).getByText('In progress', { exact: true })).toBeVisible();

		// Second approval (cfo — a third, distinct actor). Close the modal
		// (its overlay blocks the sidebar) before signing out of admin and
		// signing in as cfo on the same page.
		await page.keyboard.press('Escape');
		await expect(chain).not.toBeVisible();
		await signOut(page);
		await signInAndWait(page, tenantCfo);
		await openInvoice(page, invoiceNumber);
		await expect(page.getByRole('button', { name: 'Approve', exact: true })).toBeVisible();
		await page.getByRole('button', { name: 'Approve', exact: true }).click();

		// The chain is now satisfied — final-approval toast, and the invoice
		// has moved off ready_for_review so review actions are gone.
		await expect(
			page.getByText('Invoice approved — moving to the next stage.', { exact: false })
		).toBeVisible();
	});
});
