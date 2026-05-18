import { execFileSync } from 'node:child_process';

import { expect, test } from '../fixtures/helpers';

import { signInAndWait } from '../fixtures/helpers';

const API_BASE = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

async function authToken(page: import('@playwright/test').Page) {
	const t = await page.evaluate(() => localStorage.getItem('auth_token'));
	if (!t) throw new Error('not signed in');
	return t;
}

interface WorkflowResponse {
	id: string;
	is_active: boolean;
	steps_config: { steps: Array<{ type: string; enabled: boolean; config: Record<string, unknown> }> };
}

async function listWorkflows(page: import('@playwright/test').Page) {
	const token = await authToken(page);
	const resp = await page.request.get(`${API_BASE}/api/workflows`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
	});
	return (await resp.json()) as WorkflowResponse[];
}

async function patchWorkflow(
	page: import('@playwright/test').Page,
	id: string,
	body: Record<string, unknown>
) {
	const token = await authToken(page);
	return page.request.patch(`${API_BASE}/api/workflows/${id}`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
		data: body
	});
}

async function createInvoice(page: import('@playwright/test').Page, suffix: string) {
	const token = await authToken(page);
	const resp = await page.request.post(`${API_BASE}/api/invoices`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
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

async function completeInvoice(page: import('@playwright/test').Page, id: string) {
	const token = await authToken(page);
	const resp = await page.request.post(`${API_BASE}/api/invoices/${id}/complete`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
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
			`DELETE FROM workflow_steps WHERE instance_id IN (SELECT id FROM workflow_instances WHERE invoice_id='${id}')`,
			'-c',
			`DELETE FROM workflow_instances WHERE invoice_id='${id}'`,
			'-c',
			`DELETE FROM audit_log WHERE entity_id='${id}'`,
			'-c',
			`DELETE FROM invoices WHERE id='${id}'`
		],
		{ env: { ...process.env, PGPASSWORD: 'postgres' }, stdio: 'pipe' }
	);
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

test.describe('workflow definition drives invoice routing (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
	});

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

			const id = await createInvoice(page, `auto-${Date.now()}`);
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
