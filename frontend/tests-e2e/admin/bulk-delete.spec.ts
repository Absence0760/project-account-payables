import {
	API_BASE,
	authedTenantHeaders,
	expect,
	signInAndWait,
	tenantPsql,
	test
} from '../fixtures/helpers';

async function createUser(
	page: import('@playwright/test').Page,
	emailSuffix: string
): Promise<string> {
	const resp = await page.request.post(`${API_BASE}/api/admin/users`, {
		headers: await authedTenantHeaders(page),
		data: {
			full_name: 'Bulk Delete Test',
			email: `e2e-bulk-${emailSuffix}@test.local`,
			role_names: ['ap_clerk']
		}
	});
	return ((await resp.json()) as { id: string }).id;
}

/**
 * /admin bulk delete. The bulk endpoint is best-effort: each user id
 * is processed independently, the response splits successes from
 * failures, and a single blocked user does NOT short-circuit the
 * others. The UI exposes per-row checkboxes (current user excluded)
 * and a floating bulk-bar with Clear + Delete N.
 */

test.describe('/admin bulk delete', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
		await page.goto('/admin');
		await page.waitForLoadState('networkidle');
		await expect(page.locator('table tbody tr').first()).toBeVisible();
	});

	test('current user has no selection checkbox', async ({ page, tenantAdmin }) => {
		const youRow = page.locator('table tbody tr', { hasText: tenantAdmin.email });
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
					headers: await authedTenantHeaders(page)
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
			tenantPsql(
				"SELECT json_build_object('id', id::text, 'orig', assigned_to_id::text) "
					+ "FROM invoices WHERE status='new' LIMIT 1"
			)
		) as { id: string; orig: string | null };
		tenantPsql(`UPDATE invoices SET assigned_to_id='${blocked}' WHERE id='${invRow.id}'`);

		try {
			const headers = await authedTenantHeaders(page);
			const resp = await page.request.post(`${API_BASE}/api/admin/users/bulk-delete`, {
				headers,
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
			tenantPsql(restore);
			// Clean up the blocked user (now deletable since the reference is gone).
			await page.request.delete(`${API_BASE}/api/admin/users/${blocked}`, {
				headers: await authedTenantHeaders(page)
			});
		}
	});

	test('refusing to delete self: passing own id returns "self" failure', async ({ page }) => {
		const headers = await authedTenantHeaders(page);
		const me = (
			(await (
				await page.request.get(`${API_BASE}/api/auth/me`, {
					headers
				})
			).json()) as { id: string }
		).id;

		const resp = await page.request.post(`${API_BASE}/api/admin/users/bulk-delete`, {
			headers,
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
