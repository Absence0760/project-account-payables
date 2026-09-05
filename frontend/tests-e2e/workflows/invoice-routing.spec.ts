import {
	API_BASE,
	authedTenantHeaders,
	currentTenantSlug,
	deleteInvoicesWhere,
	expect,
	tenantHeaders,
	tenantPsql,
	test
} from '../fixtures/helpers';

interface WorkflowResponse {
	id: string;
	is_active: boolean;
	steps_config: { steps: Array<{ type: string; enabled: boolean; config: Record<string, unknown> }> };
}

async function listWorkflows(page: import('@playwright/test').Page) {
	const resp = await page.request.get(`${API_BASE}/api/workflows`, {
		headers: await authedTenantHeaders(page)
	});
	return ((await resp.json()) as { items: WorkflowResponse[] }).items;
}

async function patchWorkflow(
	page: import('@playwright/test').Page,
	id: string,
	body: Record<string, unknown>
) {
	return page.request.patch(`${API_BASE}/api/workflows/${id}`, {
		headers: await authedTenantHeaders(page),
		data: body
	});
}

async function createInvoice(
	page: import('@playwright/test').Page,
	suffix: string,
	creds?: Record<string, string>
) {
	const resp = await page.request.post(`${API_BASE}/api/invoices`, {
		headers: creds ?? (await authedTenantHeaders(page)),
		data: {
			vendor: 'E2E Routing Vendor',
			invoice_number: `WF-RT-${suffix}`,
			amount: 1234.5,
			currency: 'USD',
			status: 'new'
		}
	});
	expect(resp.status()).toBe(201);
	const body = (await resp.json()) as { id: string };
	return body.id;
}

/** Sign the tenant's seeded `manager` account in — used to create an
 *  invoice as a DIFFERENT actor than the one who completes it. The
 *  auto_approve_below threshold path degrades to human review (rather than
 *  auto-approving) when the completer is also the creator — the same
 *  segregation-of-duties check `check_segregation` raises on, just
 *  fail-soft instead of a 403 for a legitimate submission. See
 *  `services/approval_chain.violates_segregation`. */
async function managerCreds(
	page: import('@playwright/test').Page
): Promise<Record<string, string>> {
	const slug = currentTenantSlug();
	const resp = await page.request.post(`${API_BASE}/api/auth/login`, {
		headers: { 'X-Tenant-Slug': slug, 'Content-Type': 'application/json' },
		data: { email: `demo+manager@${slug}.localhost`, password: 'demo' }
	});
	expect(resp.ok(), `manager login failed (${resp.status()})`).toBe(true);
	const { access_token } = (await resp.json()) as { access_token: string };
	return tenantHeaders(access_token, slug);
}

async function completeInvoice(page: import('@playwright/test').Page, id: string) {
	const resp = await page.request.post(`${API_BASE}/api/invoices/${id}/complete`, {
		headers: await authedTenantHeaders(page)
	});
	const body = (await resp.json()) as { status: string; message?: string };
	return { status: resp.status(), body };
}

/**
 * Hard delete an invoice + its workflow instance + steps via psql.
 * The PATCH/DELETE invoice endpoint enforces immutable-status rules
 * and the auto-approved test moves the row through approved → done,
 * which the DELETE refuses (post-approval); raw SQL bypasses that.
 */
function hardDeleteInvoice(id: string): void {
	tenantPsql(
		`DELETE FROM workflow_steps WHERE instance_id IN (SELECT id FROM workflow_instances WHERE invoice_id='${id}')`
	);
	tenantPsql(`DELETE FROM workflow_instances WHERE invoice_id='${id}'`);
	// Manual create now auto-links a vendor (`match_and_link_vendor`), and an
	// unverified auto-minted vendor raises an `unverified_vendor` exception on
	// the invoice — which `exceptions.invoice_id` FK-references, so it must be
	// cleared before the invoice delete or the DELETE fails.
	tenantPsql(`DELETE FROM exceptions WHERE invoice_id='${id}'`);
	// audit_log is append-only (DB trigger, migration 0022 + seed) — never DELETE;
	// orphan rows for the removed invoice are harmless (no FK back to invoices).
	deleteInvoicesWhere(`id='${id}'`);
}

/**
 * Workflow definition drives /api/workflow/{id}/complete routing for
 * a `new` invoice:
 *
 * - approval enabled  + amount >= auto_below threshold → ready_for_review
 * - approval enabled  + amount <  auto_below           → auto-approved (status=approved)
 * - approval disabled                                  → goes straight to terminal/erp
 *
 * Each invoice freezes a workflow snapshot at creation time, so a
 * subsequent edit to the live workflow doesn't retroactively re-route
 * already-created invoices.
 */

test.describe('workflow definition drives invoice routing', () => {
	test('approval step + no auto_below: new invoice goes to ready_for_review', async ({
		page
	}) => {
		const wfs = await listWorkflows(page);
		const active = wfs.find((w) => w.is_active);
		expect(active).toBeTruthy();
		const before = active!.steps_config;

		// Set approval.required=true with no auto_approve_below.
		const stepsForReview = before.steps.map((s) =>
			s.type === 'approval'
				? {
						...s,
						enabled: true,
						config: { ...s.config, required: true, auto_approve_below: null }
					}
				: s
		);

		const created: string[] = [];
		try {
			await patchWorkflow(page, active!.id, { steps: stepsForReview });

			const id = await createInvoice(page, `review-${Date.now()}`);
			created.push(id);
			const result = await completeInvoice(page, id);
			expect(result.status).toBe(200);
			expect(result.body.status).toBe('ready_for_review');
		} finally {
			for (const id of created) hardDeleteInvoice(id);
			await patchWorkflow(page, active!.id, { steps: before.steps });
		}
	});

	test('approval step + auto_approve_below larger than amount: invoice auto-approves', async ({
		page
	}) => {
		const wfs = await listWorkflows(page);
		const active = wfs.find((w) => w.is_active);
		expect(active).toBeTruthy();
		const before = active!.steps_config;

		// Set auto_approve_below to a value above our test invoice amount (1234.5 < 5000).
		const stepsAutoApprove = before.steps.map((s) =>
			s.type === 'approval'
				? {
						...s,
						enabled: true,
						config: { ...s.config, required: true, auto_approve_below: 5000 }
					}
				: s
		);

		const created: string[] = [];
		try {
			await patchWorkflow(page, active!.id, { steps: stepsAutoApprove });

			// Created as the manager, completed as the page's own admin session
			// — different actors, so the auto-approve path's segregation check
			// doesn't degrade to human review (see managerCreds' docstring).
			const id = await createInvoice(page, `auto-${Date.now()}`, await managerCreds(page));
			created.push(id);
			const result = await completeInvoice(page, id);
			expect(result.status).toBe(200);
			expect(result.body.status).toBe('approved');
			expect(result.body.message ?? '').toContain('Auto-approved');
		} finally {
			for (const id of created) hardDeleteInvoice(id);
			await patchWorkflow(page, active!.id, { steps: before.steps });
		}
	});

	test('snapshot semantics: editing the workflow after invoice creation does NOT re-route', async ({
		page
	}) => {
		const wfs = await listWorkflows(page);
		const active = wfs.find((w) => w.is_active);
		expect(active).toBeTruthy();
		const before = active!.steps_config;

		// Phase 1: workflow has approval enabled, no auto_below.
		const phaseOne = before.steps.map((s) =>
			s.type === 'approval'
				? {
						...s,
						enabled: true,
						config: { ...s.config, required: true, auto_approve_below: null }
					}
				: s
		);

		const created: string[] = [];
		try {
			await patchWorkflow(page, active!.id, { steps: phaseOne });

			// Create an invoice — it freezes a snapshot with approval.required=true,
			// auto_below=null.
			const id = await createInvoice(page, `snap-${Date.now()}`);
			created.push(id);

			// Phase 2: flip the live workflow to auto_below=5000 — would route
			// freshly-created invoices to approved, but the existing invoice's
			// snapshot still says auto_below=null.
			const phaseTwo = before.steps.map((s) =>
				s.type === 'approval'
					? {
							...s,
							enabled: true,
							config: { ...s.config, required: true, auto_approve_below: 5000 }
						}
					: s
			);
			await patchWorkflow(page, active!.id, { steps: phaseTwo });

			// Run /complete on the snapshot-bound invoice. Snapshot says
			// auto_below=null, so it should go to ready_for_review, NOT approved.
			const result = await completeInvoice(page, id);
			expect(result.status).toBe(200);
			expect(result.body.status).toBe('ready_for_review');
		} finally {
			for (const id of created) hardDeleteInvoice(id);
			await patchWorkflow(page, active!.id, { steps: before.steps });
		}
	});
});
