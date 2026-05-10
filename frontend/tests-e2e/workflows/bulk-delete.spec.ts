import { execFileSync } from 'node:child_process';

import { expect, test } from '@playwright/test';

import { signInAndWait } from '../fixtures/helpers';

const API_BASE = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

async function authToken(page: import('@playwright/test').Page) {
	const t = await page.evaluate(() => localStorage.getItem('auth_token'));
	if (!t) throw new Error('not signed in');
	return t;
}

async function createWorkflow(
	page: import('@playwright/test').Page,
	name: string
): Promise<string> {
	const token = await authToken(page);
	const resp = await page.request.post(`${API_BASE}/api/workflows`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
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
	const token = await authToken(page);
	return page.request.delete(`${API_BASE}/api/workflows/${id}`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
	});
}

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
 * /workflows bulk delete. Same partial-success contract as users:
 * each id is processed independently and the response splits
 * successes from failures. Per-workflow reasons:
 * - default   — refusal of the seeded default workflow
 * - active    — the workflow is currently is_active=true
 * - instances — at least one workflow_instance points at it
 * - not_found — id doesn't belong to this tenant
 *
 * The UI exposes per-row checkboxes (default-row excluded) and a
 * floating bulk-bar with Clear + Delete N.
 */

test.describe('/workflows bulk delete (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
		await page.goto('/workflows');
		await page.waitForLoadState('networkidle');
	});

	test('default workflow has no selection checkbox', async ({ page }) => {
		const defaultRow = page.locator('table tbody tr', { has: page.locator('.default-badge') });
		await expect(defaultRow).toBeVisible();
		await expect(defaultRow.locator('td.checkbox-col input[type="checkbox"]')).toHaveCount(0);
	});

	test('selecting rows reveals the bulk-bar with the right count', async ({ page }) => {
		const created: string[] = [];
		try {
			created.push(await createWorkflow(page, `BulkBar A ${Date.now()}`));
			created.push(await createWorkflow(page, `BulkBar B ${Date.now()}`));
			await page.reload();
			await page.waitForLoadState('networkidle');

			// Pick the two new rows by name.
			await page
				.locator('table tbody tr', { hasText: 'BulkBar A' })
				.locator('td.checkbox-col input[type="checkbox"]')
				.check();
			await page
				.locator('table tbody tr', { hasText: 'BulkBar B' })
				.locator('td.checkbox-col input[type="checkbox"]')
				.check();

			const bar = page.locator('.bulk-bar');
			await expect(bar).toBeVisible();
			await expect(bar.locator('.bulk-count')).toHaveText('2 selected');

			await bar.getByRole('button', { name: 'Clear' }).click();
			await expect(bar).toBeHidden();
		} finally {
			for (const id of created) await deleteWorkflow(page, id);
		}
	});

	test('bulk Delete drops every selected workflow from the list', async ({ page }) => {
		const ts = Date.now();
		const a = await createWorkflow(page, `Bulk Del A ${ts}`);
		const b = await createWorkflow(page, `Bulk Del B ${ts}`);
		await page.reload();
		await page.waitForLoadState('networkidle');
		const beforeRows = await page.locator('table tbody tr').count();

		await page
			.locator('table tbody tr', { hasText: `Bulk Del A ${ts}` })
			.locator('td.checkbox-col input[type="checkbox"]')
			.check();
		await page
			.locator('table tbody tr', { hasText: `Bulk Del B ${ts}` })
			.locator('td.checkbox-col input[type="checkbox"]')
			.check();

		// Armed-confirm pattern: first click arms, second click commits.
		const bar = page.locator('.bulk-bar');
		await bar.getByRole('button', { name: /^Delete 2$/ }).click();
		const posted = page.waitForResponse(
			(r) =>
				r.url().endsWith('/api/workflows/bulk-delete') &&
				r.request().method() === 'POST' &&
				r.status() === 200
		);
		await bar.getByRole('button', { name: /^Confirm Delete 2$/ }).click();
		const resp = await posted;
		const body = (await resp.json()) as { deleted: string[]; failed: unknown[] };
		expect(body.deleted.sort()).toEqual([a, b].sort());
		expect(body.failed).toEqual([]);
		await expect(page.locator('table tbody tr')).toHaveCount(beforeRows - 2);
	});

	test('partial: blocked workflows surface their reason; deletable ones go through', async ({
		page
	}) => {
		const token = await authToken(page);

		// One workflow we'll wedge with a fake instance, and one that's
		// freely deletable.
		const wedged = await createWorkflow(page, `Wedged ${Date.now()}`);
		const free = await createWorkflow(page, `Free ${Date.now()}`);

		// Need a real invoice to satisfy the FK on workflow_instances.invoice_id.
		const invoiceId = execFileSync(
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
				'SELECT id FROM invoices LIMIT 1'
			],
			{ env: { ...process.env, PGPASSWORD: 'postgres' }, stdio: 'pipe' }
		)
			.toString()
			.trim();

		const instanceId = crypto.randomUUID();
		sql(
			`INSERT INTO workflow_instances (id, correlation_id, definition_id, invoice_id, current_step, state, steps_config_snapshot) `
				+ `VALUES ('${instanceId}', gen_random_uuid(), '${wedged}', '${invoiceId}', 0, 'active', '{}'::jsonb)`
		);

		try {
			const resp = await page.request.post(`${API_BASE}/api/workflows/bulk-delete`, {
				headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
				data: { workflow_ids: [wedged, free] }
			});
			expect(resp.status()).toBe(200);
			const body = (await resp.json()) as {
				deleted: string[];
				failed: Array<{ workflow_id: string; reason: string; instance_count: number | null }>;
			};
			expect(body.deleted).toEqual([free]);
			expect(body.failed.length).toBe(1);
			expect(body.failed[0].workflow_id).toBe(wedged);
			expect(body.failed[0].reason).toBe('instances');
			expect(body.failed[0].instance_count).toBeGreaterThanOrEqual(1);
		} finally {
			sql(`DELETE FROM workflow_instances WHERE id='${instanceId}'`);
			await deleteWorkflow(page, wedged);
		}
	});

	test('attempting to bulk-delete the default workflow returns "default" failure', async ({
		page
	}) => {
		const token = await authToken(page);
		const wfsResp = await page.request.get(`${API_BASE}/api/workflows`, {
			headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
		});
		const wfs = (await wfsResp.json()) as Array<{ id: string; is_default: boolean }>;
		const defaultId = wfs.find((w) => w.is_default)!.id;

		const resp = await page.request.post(`${API_BASE}/api/workflows/bulk-delete`, {
			headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
			data: { workflow_ids: [defaultId] }
		});
		expect(resp.status()).toBe(200);
		const body = (await resp.json()) as {
			deleted: string[];
			failed: Array<{ workflow_id: string; reason: string }>;
		};
		expect(body.deleted).toEqual([]);
		expect(body.failed[0].reason).toBe('default');
	});
});
