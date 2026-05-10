import { execFileSync } from 'node:child_process';

import { expect, test } from '@playwright/test';

import { signInAndWait } from '../fixtures/helpers';

const API_BASE = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

async function authToken(page: import('@playwright/test').Page) {
	const t = await page.evaluate(() => localStorage.getItem('auth_token'));
	if (!t) throw new Error('not signed in');
	return t;
}

async function createUser(
	page: import('@playwright/test').Page,
	email: string
): Promise<string> {
	const token = await authToken(page);
	const resp = await page.request.post(`${API_BASE}/api/admin/users`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
		data: { full_name: 'Delete Safety Test', email, role_names: ['ap_clerk'] }
	});
	return ((await resp.json()) as { id: string }).id;
}

async function deleteUser(page: import('@playwright/test').Page, id: string) {
	const token = await authToken(page);
	return page.request.delete(`${API_BASE}/api/admin/users/${id}`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
	});
}

/**
 * Run a SQL statement directly against ap_acme. Tests use this to
 * stage an "in-flight reference" against a freshly-created user
 * (e.g. an open invoice assignment) and to clean up afterwards.
 */
function sql(query: string): void {
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
			query
		],
		{ env: { ...process.env, PGPASSWORD: 'postgres' }, stdio: 'pipe' }
	);
}

/**
 * /admin user-delete cascade safety. Deleting a user who is still
 * referenced by in-flight work would silently orphan the references —
 * an open invoice's `assigned_to_id` pointing at a deleted user, or a
 * pending workflow_step waiting on a deleted approver — leaving the
 * UI to render `null` and the workflow stuck. The DELETE endpoint
 * refuses with a 409 + structured reference counts; the admin must
 * reassign first.
 *
 * Audit-log / payment-initiator / invoice-uploader references are
 * INTENTIONALLY excluded from the safety check — those must survive
 * the user (audit data is append-only by design).
 */

test.describe('/admin user-delete safety (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
	});

	test('user with no references can be deleted', async ({ page }) => {
		const id = await createUser(page, `e2e-safe-${Date.now()}@acme.test`);
		const resp = await deleteUser(page, id);
		expect(resp.status()).toBe(204);
	});

	test('refuses delete when user is assigned to an open invoice', async ({ page }) => {
		const id = await createUser(page, `e2e-inv-${Date.now()}@acme.test`);

		// Pick a non-terminal invoice and stash its current assigned_to_id
		// so we can revert. Using a `new`/`ready_for_review` row keeps the
		// safety check tripped (status is in the "open" set).
		const before = JSON.parse(
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
					'-tAc',
					"SELECT json_build_object('id', id::text, 'orig', assigned_to_id::text) "
						+ "FROM invoices WHERE status='new' LIMIT 1"
				],
				{ env: { ...process.env, PGPASSWORD: 'postgres' }, stdio: 'pipe' }
			).toString()
		) as { id: string; orig: string | null };

		try {
			sql(`UPDATE invoices SET assigned_to_id='${id}' WHERE id='${before.id}'`);
			const resp = await deleteUser(page, id);
			expect(resp.status()).toBe(409);
			const body = (await resp.json()) as {
				detail: {
					message: string;
					references: {
						open_invoice_assignments: number;
						pending_approval_steps: number;
						active_workflow_approver_in: number;
					};
				};
			};
			expect(body.detail.references.open_invoice_assignments).toBeGreaterThanOrEqual(1);
		} finally {
			// Revert assignment, then delete the throwaway user.
			const restore = before.orig
				? `UPDATE invoices SET assigned_to_id='${before.orig}' WHERE id='${before.id}'`
				: `UPDATE invoices SET assigned_to_id=NULL WHERE id='${before.id}'`;
			sql(restore);
			await deleteUser(page, id);
		}
	});

	test('refuses delete when user has a pending workflow step assigned', async ({ page }) => {
		const id = await createUser(page, `e2e-step-${Date.now()}@acme.test`);
		const token = await authToken(page);

		// POST /api/invoices creates a workflow_instance per
		// services/workflow_engine.create_workflow_instance, which is the
		// FK target we need to insert a synthetic step against.
		const invResp = await page.request.post(`${API_BASE}/api/invoices`, {
			headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
			data: {
				vendor: 'Delete Safety Step Vendor',
				invoice_number: `DSS-${Date.now()}`,
				amount: 100,
				status: 'new'
			}
		});
		const invoice = (await invResp.json()) as { id: string };

		const instanceId = execFileSync(
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
				'-tAc',
				`SELECT id FROM workflow_instances WHERE invoice_id='${invoice.id}'`
			],
			{ env: { ...process.env, PGPASSWORD: 'postgres' }, stdio: 'pipe' }
		)
			.toString()
			.trim();

		// Insert a synthetic pending step pointing at our user. Generate the
		// id client-side so we don't have to parse psql's "INSERT 0 1" tail.
		const stepRow = crypto.randomUUID();
		sql(
			`INSERT INTO workflow_steps (id, correlation_id, instance_id, step_number, step_type, assigned_to) `
				+ `VALUES ('${stepRow}', gen_random_uuid(), '${instanceId}', 99, 'approval', '${id}')`
		);

		try {
			const resp = await deleteUser(page, id);
			expect(resp.status()).toBe(409);
			const body = (await resp.json()) as {
				detail: { references: { pending_approval_steps: number } };
			};
			expect(body.detail.references.pending_approval_steps).toBeGreaterThanOrEqual(1);
		} finally {
			sql(`DELETE FROM workflow_steps WHERE id='${stepRow}'`);
			sql(`DELETE FROM workflow_instances WHERE id='${instanceId}'`);
			sql(`DELETE FROM audit_log WHERE entity_id='${invoice.id}'`);
			sql(`DELETE FROM invoices WHERE id='${invoice.id}'`);
			await deleteUser(page, id);
		}
	});

	test('refuses delete when user is in an active workflow def approver_ids', async ({
		page
	}) => {
		const id = await createUser(page, `e2e-defn-${Date.now()}@acme.test`);
		const token = await authToken(page);

		// Get the active workflow + put the user into approval.approver_ids.
		const wfsResp = await page.request.get(`${API_BASE}/api/workflows`, {
			headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
		});
		const wfs = (await wfsResp.json()) as Array<{
			id: string;
			is_active: boolean;
			steps_config: { steps: Array<{ type: string; config: Record<string, unknown> }> };
		}>;
		const active = wfs.find((w) => w.is_active);
		expect(active).toBeTruthy();
		const before = active!.steps_config;

		const withApprover = before.steps.map((s) =>
			s.type === 'approval'
				? { ...s, config: { ...s.config, approver_ids: [id] } }
				: s
		);

		try {
			await page.request.patch(`${API_BASE}/api/workflows/${active!.id}`, {
				headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
				data: { steps: withApprover }
			});

			const resp = await deleteUser(page, id);
			expect(resp.status()).toBe(409);
			const body = (await resp.json()) as {
				detail: { references: { active_workflow_approver_in: number } };
			};
			expect(body.detail.references.active_workflow_approver_in).toBeGreaterThanOrEqual(1);
		} finally {
			await page.request.patch(`${API_BASE}/api/workflows/${active!.id}`, {
				headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
				data: { steps: before.steps }
			});
			await deleteUser(page, id);
		}
	});
});
