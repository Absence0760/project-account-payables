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
	emailSuffix: string
): Promise<string> {
	const token = await authToken(page);
	const resp = await page.request.post(`${API_BASE}/api/admin/users`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
		data: {
			full_name: 'Bulk Delete Test',
			email: `e2e-bulk-${emailSuffix}@acme.test`,
			role_names: ['ap_clerk']
		}
	});
	return ((await resp.json()) as { id: string }).id;
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
 * /admin bulk delete. The bulk endpoint is best-effort: each user id
 * is processed independently, the response splits successes from
 * failures, and a single blocked user does NOT short-circuit the
 * others. The UI exposes per-row checkboxes (current user excluded)
 * and a floating bulk-bar with Clear + Delete N.
 */

test.describe('/admin bulk delete (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
		await page.goto('/admin');
		await page.waitForLoadState('networkidle');
		await expect(page.locator('table tbody tr').first()).toBeVisible();
	});

	test('current user has no selection checkbox', async ({ page }) => {
		const youRow = page.locator('table tbody tr', { hasText: 'demo@acme.com' });
		await expect(youRow.locator('.you-badge')).toBeVisible();
		await expect(youRow.locator('td.checkbox-col input[type="checkbox"]')).toHaveCount(0);
	});

	test('selecting rows reveals the bulk-bar with the right count', async ({ page }) => {
		const created: string[] = [];
		try {
			created.push(await createUser(page, `bar-${Date.now()}-1`));
			created.push(await createUser(page, `bar-${Date.now()}-2`));
			await page.reload();
			await page.waitForLoadState('networkidle');

			// Pick the two newest rows (top of the table) — they're the
			// just-created users.
			await page
				.locator('table tbody tr td.checkbox-col input[type="checkbox"]')
				.first()
				.check();
			await page
				.locator('table tbody tr td.checkbox-col input[type="checkbox"]')
				.nth(1)
				.check();

			const bar = page.locator('.bulk-bar');
			await expect(bar).toBeVisible();
			await expect(bar.locator('.bulk-count')).toHaveText('2 selected');

			await bar.getByRole('button', { name: 'Clear' }).click();
			await expect(bar).toBeHidden();
		} finally {
			for (const id of created) {
				await page.request.delete(`${API_BASE}/api/admin/users/${id}`, {
					headers: {
						Authorization: `Bearer ${await authToken(page)}`,
						'X-Tenant-Slug': 'acme'
					}
				});
			}
		}
	});

	test('bulk-bar Delete drops every selected user from the list', async ({ page }) => {
		const ts = Date.now();
		const a = await createUser(page, `del-${ts}-a`);
		const b = await createUser(page, `del-${ts}-b`);

		await page.reload();
		await page.waitForLoadState('networkidle');
		const beforeRows = await page.locator('table tbody tr').count();

		// Select via API id — find the row by email substring.
		await page
			.locator('table tbody tr', { hasText: `e2e-bulk-del-${ts}-a` })
			.locator('td.checkbox-col input[type="checkbox"]')
			.check();
		await page
			.locator('table tbody tr', { hasText: `e2e-bulk-del-${ts}-b` })
			.locator('td.checkbox-col input[type="checkbox"]')
			.check();

		const bar = page.locator('.bulk-bar');
		await expect(bar).toBeVisible();

		// BulkDeleteButton uses an armed-confirm pattern (matches /invoices):
		// first click flips icon to a checkmark, second click commits.
		await bar.getByRole('button', { name: /^Delete 2$/ }).click();
		const posted = page.waitForResponse(
			(r) =>
				r.url().endsWith('/api/admin/users/bulk-delete') &&
				r.request().method() === 'POST' &&
				r.status() === 200
		);
		await bar.getByRole('button', { name: /^Confirm Delete 2$/ }).click();
		const resp = await posted;
		const body = (await resp.json()) as { deleted: string[]; failed: unknown[] };
		expect(body.deleted.sort()).toEqual([a, b].sort());
		expect(body.failed).toEqual([]);

		// Table shrinks by 2; rows for the deleted users are gone.
		await expect(page.locator('table tbody tr')).toHaveCount(beforeRows - 2);
		await expect(
			page.locator('table tbody tr', { hasText: `e2e-bulk-del-${ts}-a` })
		).toHaveCount(0);
	});

	test('partial: blocked users stay; deletable users go through', async ({ page }) => {
		const ts = Date.now();
		const blocked = await createUser(page, `mix-${ts}-blocked`);
		const deletable = await createUser(page, `mix-${ts}-deletable`);

		// Stash + clobber an open invoice's assigned_to_id so `blocked`
		// is referenced.
		const invRow = JSON.parse(
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
		sql(`UPDATE invoices SET assigned_to_id='${blocked}' WHERE id='${invRow.id}'`);

		try {
			const token = await authToken(page);
			const resp = await page.request.post(`${API_BASE}/api/admin/users/bulk-delete`, {
				headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
				data: { user_ids: [blocked, deletable] }
			});
			expect(resp.status()).toBe(200);
			const body = (await resp.json()) as {
				deleted: string[];
				failed: Array<{
					user_id: string;
					reason: string;
					references: {
						open_invoice_assignments: number;
						pending_approval_steps: number;
						active_workflow_approver_in: number;
					} | null;
				}>;
			};
			expect(body.deleted).toEqual([deletable]);
			expect(body.failed.length).toBe(1);
			expect(body.failed[0].user_id).toBe(blocked);
			expect(body.failed[0].reason).toBe('blocked');
			expect(body.failed[0].references?.open_invoice_assignments).toBeGreaterThanOrEqual(1);
		} finally {
			const restore = invRow.orig
				? `UPDATE invoices SET assigned_to_id='${invRow.orig}' WHERE id='${invRow.id}'`
				: `UPDATE invoices SET assigned_to_id=NULL WHERE id='${invRow.id}'`;
			sql(restore);
			// Clean up the blocked user (now deletable since the reference is gone).
			const token = await authToken(page);
			await page.request.delete(`${API_BASE}/api/admin/users/${blocked}`, {
				headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
			});
		}
	});

	test('refusing to delete self: passing own id returns "self" failure', async ({ page }) => {
		const token = await authToken(page);
		const me = (
			(await (
				await page.request.get(`${API_BASE}/api/auth/me`, {
					headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
				})
			).json()) as { id: string }
		).id;

		const resp = await page.request.post(`${API_BASE}/api/admin/users/bulk-delete`, {
			headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' },
			data: { user_ids: [me] }
		});
		expect(resp.status()).toBe(200);
		const body = (await resp.json()) as {
			deleted: string[];
			failed: Array<{ user_id: string; reason: string }>;
		};
		expect(body.deleted).toEqual([]);
		expect(body.failed[0]?.reason).toBe('self');
	});
});
