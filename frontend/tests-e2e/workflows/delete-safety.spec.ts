import {
	API_BASE,
	authedTenantHeaders,
	expect,
	signInAndWait,
	tenantPsql,
	test
} from '../fixtures/helpers';

async function createWorkflow(
	page: import('@playwright/test').Page,
	name: string
): Promise<string> {
	const resp = await page.request.post(`${API_BASE}/api/workflows`, {
		headers: await authedTenantHeaders(page),
		data: {
			name,
			steps: [
				{
					number: 1,
					type: 'extraction',
					name: 'Data Extraction',
					enabled: true,
					config: { auto_approve_enabled: false, auto_approve_threshold: 0.95 }
				}
			]
		}
	});
	return ((await resp.json()) as { id: string }).id;
}

async function deleteWorkflow(page: import('@playwright/test').Page, id: string) {
	return page.request.delete(`${API_BASE}/api/workflows/${id}`, {
		headers: await authedTenantHeaders(page)
	});
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

/**
 * Workflow delete cascade safety. Three independent guards:
 * 1. is_default — refuses deletion of the default workflow (existed)
 * 2. is_active  — refuses deletion of the currently-active workflow
 *    (NEW — must deactivate first to avoid the org being left with
 *    no active workflow at all)
 * 3. workflow_instances — refuses deletion when any invoice has this
 *    definition as its snapshot source. Previously the underlying
 *    FK (NO ACTION) surfaced as a 500; now returns a clean 409 with
 *    instance_count.
 */

test.describe('/workflows delete cascade safety', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
	});

	test('refuses delete of an active (non-default) workflow', async ({ page }) => {
		// Create a new workflow + activate it. Activation deactivates the
		// seeded default, so we revert at the end.
		const wfsBefore = await page.request.get(`${API_BASE}/api/workflows`, {
			headers: await authedTenantHeaders(page)
		});
		const before = (await wfsBefore.json()) as Array<{ id: string; is_default: boolean }>;
		const defaultWf = before.find((w) => w.is_default)!;

		const id = await createWorkflow(page, `Active Safety ${Date.now()}`);
		try {
			await patchWorkflow(page, id, { is_active: true });

			const resp = await deleteWorkflow(page, id);
			expect(resp.status()).toBe(409);
			const body = (await resp.json()) as { detail: string };
			expect(body.detail).toMatch(/active workflow/i);
		} finally {
			await patchWorkflow(page, id, { is_active: false });
			await patchWorkflow(page, defaultWf.id, { is_active: true });
			await deleteWorkflow(page, id);
		}
	});

	test('refuses delete when workflow_instances point at it (clean 409, not FK 500)', async ({
		page
	}) => {
		const id = await createWorkflow(page, `Instance Safety ${Date.now()}`);

		// Insert a synthetic instance pointing at this definition. The
		// invoice_id FK requires a real invoice — pick the first existing
		// one in the tenant DB.
		const invoiceId = tenantPsql('SELECT id FROM invoices LIMIT 1').trim();
		expect(invoiceId).toBeTruthy();

		const instanceRow = crypto.randomUUID();
		// Use a fresh correlation_id to avoid colliding with the invoice's own.
		tenantPsql(
			`INSERT INTO workflow_instances (id, correlation_id, definition_id, invoice_id, current_step, state, steps_config_snapshot) `
				+ `VALUES ('${instanceRow}', gen_random_uuid(), '${id}', '${invoiceId}', 0, 'active', '{}'::jsonb)`
		);

		try {
			const resp = await page.request.delete(`${API_BASE}/api/workflows/${id}`, {
				headers: await authedTenantHeaders(page)
			});
			expect(resp.status()).toBe(409);
			const body = (await resp.json()) as {
				detail: { message: string; instance_count: number };
			};
			expect(body.detail.instance_count).toBeGreaterThanOrEqual(1);
			expect(body.detail.message).toMatch(/in-flight invoice/);
		} finally {
			tenantPsql(`DELETE FROM workflow_instances WHERE id='${instanceRow}'`);
			await deleteWorkflow(page, id);
		}
	});

	test('inactive non-default workflow with no instances deletes cleanly', async ({ page }) => {
		const id = await createWorkflow(page, `Plain Delete ${Date.now()}`);
		// Newly created workflows start is_active=false, is_default=false, with
		// zero instances → all three safety guards clear; delete returns 204.
		const resp = await deleteWorkflow(page, id);
		expect(resp.status()).toBe(204);
	});
});
