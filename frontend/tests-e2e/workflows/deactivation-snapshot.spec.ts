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

async function createWorkflow(
	page: import('@playwright/test').Page,
	name: string,
	steps: Array<{ type: string; enabled: boolean; config: Record<string, unknown> }>
): Promise<string> {
	const resp = await page.request.post(`${API_BASE}/api/workflows`, {
		headers: await authedTenantHeaders(page),
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

async function createInvoice(
	page: import('@playwright/test').Page,
	suffix: string,
	creds?: Record<string, string>
) {
	const resp = await page.request.post(`${API_BASE}/api/invoices`, {
		headers: creds ?? (await authedTenantHeaders(page)),
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
	return ((await resp.json()) as { status: string }).status;
}

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

test.describe('workflow deactivation snapshot semantics', () => {
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
			// auto-approves immediately. Created as the manager, completed as the
			// page's own admin session — different actors, so the auto-approve
			// path's segregation check doesn't degrade to human review (see
			// managerCreds' docstring).
			const y = await createInvoice(page, `y-${Date.now()}`, await managerCreds(page));
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
					headers: await authedTenantHeaders(page)
				});
			} else {
				// B never created → just restore steps on the seeded workflow.
				await patchWorkflow(page, seedActive.id, { steps: seedSteps });
			}
		}
	});

	test('deactivating the last active workflow is refused, and snapshots survive a legitimate swap', async ({
		page
	}) => {
		// Two things at once.
		//
		// (1) The org may NOT be left with zero active workflows. It used to
		// be allowed, and `get_or_create_workflow_definition` would then
		// lazily mint an "Invoice Processing" stub with every step disabled —
		// so the next invoice sailed `new → done` with no approval, no
		// signature, no audit row and no CFO gate. (Worse, when a shared
		// default already exists that insert violates
		// `uq_workflow_definitions_one_default` and 500s invoice upload.)
		// The PATCH is now a 409.
		//
		// (2) The snapshot invariant this spec exists for is unchanged: an
		// invoice completes from its OWN frozen snapshot even after the
		// definition it came from stops being active. We reach that state the
		// way a user actually can — by activating a replacement.
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
		const cleanupWorkflows: string[] = [];
		try {
			await patchWorkflow(page, seedActive.id, { steps: stepsForReview });

			// Create invoice X — snapshot captured.
			const x = await createInvoice(page, `nodef-${Date.now()}`);
			created.push(x);

			// (1) Refused — this is the only active workflow.
			const refused = await patchWorkflow(page, seedActive.id, { is_active: false });
			expect(refused.status()).toBe(409);
			const stillActive = await listWorkflows(page);
			expect(stillActive.find((w) => w.id === seedActive.id)!.is_active).toBe(true);

			// (2) Activate a replacement — that legitimately deactivates the
			// seeded one, leaving invoice X's snapshot orphaned from any
			// active definition, which is the state under test.
			const replacementId = await createWorkflow(page, `replacement-${Date.now()}`, seedSteps);
			cleanupWorkflows.push(replacementId);
			await patchWorkflow(page, replacementId, { is_active: true });
			const wfsMid = await listWorkflows(page);
			expect(wfsMid.find((w) => w.id === seedActive.id)!.is_active).toBe(false);

			// Invoice X completes via its snapshot, unaffected by the
			// deactivation.
			const xStatus = await completeInvoice(page, x);
			expect(xStatus).toBe('ready_for_review');
		} finally {
			for (const id of created) hardDeleteInvoice(id);
			// Restore the seeded workflow FIRST (activating it deactivates the
			// replacement), then the replacement can be deleted — delete
			// refuses an active definition.
			await patchWorkflow(page, seedActive.id, {
				is_active: true,
				steps: seedSteps
			});
			for (const id of cleanupWorkflows) {
				await page.request.delete(`${API_BASE}/api/workflows/${id}`, {
					headers: await authedTenantHeaders(page)
				});
			}
		}
	});
});
