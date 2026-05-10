import { execFileSync } from 'node:child_process';

import { expect, test } from '@playwright/test';

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

async function createWorkflow(
	page: import('@playwright/test').Page,
	name: string,
	steps: Array<{ type: string; enabled: boolean; config: Record<string, unknown> }>
): Promise<string> {
	const token = await authToken(page);
	const resp = await page.request.post(`${API_BASE}/api/workflows`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
		data: {
			name,
			steps: steps.map((s, i) => ({
				number: i + 1,
				name: s.type,
				...s
			}))
		}
	});
	return ((await resp.json()) as { id: string }).id;
}

async function createInvoice(page: import('@playwright/test').Page, suffix: string) {
	const token = await authToken(page);
	const resp = await page.request.post(`${API_BASE}/api/invoices`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
		data: {
			vendor: 'Snapshot Test Vendor',
			invoice_number: `SNAP-${suffix}`,
			amount: 1234.5,
			currency: 'USD',
			status: 'new'
		}
	});
	return ((await resp.json()) as { id: string }).id;
}

async function completeInvoice(page: import('@playwright/test').Page, id: string) {
	const token = await authToken(page);
	const resp = await page.request.post(`${API_BASE}/api/invoices/${id}/complete`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
	});
	return ((await resp.json()) as { status: string }).status;
}

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
 * Workflow deactivation must NOT re-route in-flight invoices. When
 * an invoice is created, its WorkflowInstance freezes a
 * `steps_config_snapshot` from whatever workflow was active at that
 * moment. Subsequent activation of a different workflow (which
 * deactivates the original by the one-active invariant) only affects
 * NEW invoices created from that point on. Existing in-flight
 * invoices keep routing per their snapshot until they reach a
 * terminal state.
 *
 * Concretely:
 *  - Workflow A (auto_approve_below=null) is active.
 *  - Invoice X is created — snapshot captures auto_approve_below=null.
 *  - Workflow B (auto_approve_below=999_999) is created and activated.
 *    The one-active invariant deactivates A.
 *  - Invoice X.complete() → ready_for_review (per A's snapshot).
 *  - A new invoice Y is created — snapshot captures
 *    auto_approve_below=999_999.
 *  - Invoice Y.complete() → approved (per B's snapshot).
 */

test.describe('workflow deactivation snapshot semantics (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
	});

	test('deactivated workflow keeps routing its in-flight invoices; new invoices use the now-active one', async ({
		page
	}) => {
		const wfsBefore = await listWorkflows(page);
		const seedActive = wfsBefore.find((w) => w.is_active)!;
		const seedSteps = seedActive.steps_config.steps;

		// Set the seeded workflow to "approval required, no auto-approve"
		// so an invoice created against it will route to ready_for_review.
		const stepsForReview = seedSteps.map((s) =>
			s.type === 'approval'
				? {
						...s,
						enabled: true,
						config: { ...s.config, required: true, auto_approve_below: null }
					}
				: s
		);

		const created: string[] = [];
		let bId: string | null = null;
		try {
			await patchWorkflow(page, seedActive.id, { steps: stepsForReview });

			// Create invoice X — captures snapshot with auto_approve_below=null.
			const x = await createInvoice(page, `x-${Date.now()}`);
			created.push(x);

			// Create + activate workflow B with auto_approve_below=999_999. Per
			// the one-active invariant, this deactivates the seeded workflow.
			bId = await createWorkflow(page, `Snapshot B ${Date.now()}`, [
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
						auto_approve_below: 999_999,
						approver_strategy: 'manual',
						approver_ids: []
					}
				},
				{
					type: 'erp_export',
					enabled: true,
					config: { erp_system: 'default', export_format: 'json', endpoint_url: '' }
				}
			]);
			await patchWorkflow(page, bId, { is_active: true });

			// Confirm the seeded workflow is now inactive.
			const wfsMid = await listWorkflows(page);
			const seedAfter = wfsMid.find((w) => w.id === seedActive.id)!;
			expect(seedAfter.is_active).toBe(false);
			const bAfter = wfsMid.find((w) => w.id === bId)!;
			expect(bAfter.is_active).toBe(true);

			// Invoice X — its frozen snapshot says auto_approve_below=null,
			// so /complete must route to ready_for_review even though the
			// snapshot's source workflow is now deactivated.
			const xStatus = await completeInvoice(page, x);
			expect(xStatus).toBe('ready_for_review');

			// New invoice Y — picks up B's config (auto_approve_below=999_999),
			// auto-approves immediately.
			const y = await createInvoice(page, `y-${Date.now()}`);
			created.push(y);
			const yStatus = await completeInvoice(page, y);
			expect(yStatus).toBe('approved');
		} finally {
			for (const id of created) hardDeleteInvoice(id);
			if (bId) {
				// Restore: deactivate B, reactivate the seeded default (with its
				// original steps), then delete B.
				await patchWorkflow(page, bId, { is_active: false });
				await patchWorkflow(page, seedActive.id, {
					is_active: true,
					steps: seedSteps
				});
				await page.request.delete(`${API_BASE}/api/workflows/${bId}`, {
					headers: {
						Authorization: `Bearer ${await authToken(page)}`,
						'X-Tenant-Slug': 'acme'
					}
				});
			} else {
				// B never created → just restore steps on the seeded workflow.
				await patchWorkflow(page, seedActive.id, { steps: seedSteps });
			}
		}
	});

	test('explicitly deactivating the active workflow without a replacement still preserves snapshots', async ({
		page
	}) => {
		// This is the "no other workflow active" edge case. PATCH the
		// active workflow with is_active=false. The org now has no active
		// workflow at all. Existing invoices still complete via their
		// snapshot; new invoices get a definition from
		// get_or_create_workflow_definition (which auto-creates one if
		// none active — see workflow_engine.get_or_create_workflow_definition).
		const wfsBefore = await listWorkflows(page);
		const seedActive = wfsBefore.find((w) => w.is_active)!;
		const seedSteps = seedActive.steps_config.steps;

		const stepsForReview = seedSteps.map((s) =>
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
			await patchWorkflow(page, seedActive.id, { steps: stepsForReview });

			// Create invoice X — snapshot captured.
			const x = await createInvoice(page, `nodef-${Date.now()}`);
			created.push(x);

			// Deactivate the seeded workflow directly. The one-active
			// invariant kicks in only when activating something — explicitly
			// setting is_active=false is allowed and leaves the org with
			// zero active workflows.
			await patchWorkflow(page, seedActive.id, { is_active: false });
			const wfsMid = await listWorkflows(page);
			expect(wfsMid.find((w) => w.id === seedActive.id)!.is_active).toBe(false);

			// Invoice X completes via its snapshot, unaffected by the
			// deactivation.
			const xStatus = await completeInvoice(page, x);
			expect(xStatus).toBe('ready_for_review');
		} finally {
			for (const id of created) hardDeleteInvoice(id);
			await patchWorkflow(page, seedActive.id, {
				is_active: true,
				steps: seedSteps
			});
		}
	});
});
