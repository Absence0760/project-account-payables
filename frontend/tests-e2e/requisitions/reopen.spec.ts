import {
	API_BASE,
	authedTenantHeaders,
	currentTenantSlug,
	expect,
	signInAndWait,
	tenantPsql,
	test
} from '../fixtures/helpers';

/**
 * `/requisitions` — the rework loop for a REJECTED requisition.
 *
 * `POST /api/requisitions/{id}/reopen` (`rejected -> draft`) has always existed
 * and the lifecycle diagram has always documented it, but no frontend caller
 * ever reached it. From `rejected` nothing else moves — `submit` only leaves
 * `draft`/`submitted`, `cancel` isn't reachable, `PATCH` is draft-only — so the
 * buyer's only exit was DELETE and a full re-key of every line.
 *
 * Two things this locks besides "the button exists":
 *   - the armed confirm names the DESTINATION (`Reopen to Draft`), not a bare
 *     "Confirm": a reopened requisition is back in DRAFT and still owes a
 *     Submit click, unlike the sibling /intake reopen which lands in `Open`;
 *   - the role gate is the CREATE set. `require_roles(ADMIN, AP_MANAGER,
 *     AP_CLERK)` on the route — NOT the decide set, which includes the CFO but
 *     not the clerk. A CFO may reject a requisition and may not reopen one, so
 *     the button must be absent for them rather than 403 on click.
 */

type Requisition = { id: string; requisition_number: string; status: string };

async function createRequisition(
	page: import('@playwright/test').Page,
	tag: string,
	headers: Record<string, string>
): Promise<Requisition> {
	const requisition_number = `RQ-REOPEN-${tag}-${Date.now()}`;
	const resp = await page.request.post(`${API_BASE}/api/requisitions`, {
		headers,
		data: {
			requisition_number,
			title: 'Rework-loop fixture',
			department: null,
			needed_by: null,
			justification: null,
			currency: 'USD',
			notes: null,
			line_items: [{ description: 'Widget', quantity: 1, unit_price: 9 }]
		}
	});
	expect(resp.status(), 'create requisition').toBe(201);
	return (await resp.json()) as Requisition;
}

async function createRejectedRequisition(
	page: import('@playwright/test').Page,
	tag: string,
	headers?: Record<string, string>
): Promise<Requisition> {
	const h = headers ?? (await authedTenantHeaders(page));
	const req = await createRequisition(page, tag, h);

	const submitted = await page.request.post(`${API_BASE}/api/requisitions/${req.id}/submit`, {
		headers: h,
		data: {}
	});
	expect(submitted.status(), 'submit requisition').toBe(200);

	// `reject` carries no segregation check (only `approve` does), so the same
	// admin that raised the row can reject it.
	const rejected = await page.request.post(`${API_BASE}/api/requisitions/${req.id}/reject`, {
		headers: h,
		data: { reason: 'Wrong cost centre' }
	});
	expect(rejected.status(), 'reject requisition').toBe(200);
	expect(((await rejected.json()) as Requisition).status).toBe('rejected');

	return { ...req, status: 'rejected' };
}

function deleteRequisition(id: string): void {
	tenantPsql(`DELETE FROM requisition_line_items WHERE requisition_id='${id}'`);
	tenantPsql(`DELETE FROM purchase_requisitions WHERE id='${id}'`);
}

test.describe('/requisitions reopen (rework loop)', () => {
	test('a rejected requisition reopens to Draft from the row', async ({ page }) => {
		const req = await createRejectedRequisition(page, 'ui');
		try {
			await page.goto('/requisitions?status=rejected');

			const row = page.locator('table tbody tr', { hasText: req.requisition_number });
			await expect(row).toBeVisible();
			await expect(row.locator('.badge.rejected')).toBeVisible();

			// One click arms; the armed copy names where the row lands.
			const reopen = row.getByRole('button', { name: 'Reopen', exact: true });
			await expect(reopen).toBeVisible();
			await reopen.click();
			await expect(row.getByRole('button', { name: 'Reopen to Draft' })).toBeVisible();

			// Second click commits. The row is patched in place (no refetch), so it
			// stays on screen and flips to `draft` — and Submit is offered again,
			// which is exactly what the strand denied.
			await row.getByRole('button', { name: 'Reopen to Draft' }).click();
			await expect(row.locator('.badge.draft')).toBeVisible();
			await expect(row.getByRole('button', { name: 'Submit' })).toBeVisible();
			await expect(row.getByRole('button', { name: /^Reopen/ })).toHaveCount(0);

			// The server agrees — the transition is `rejected -> draft`, persisted.
			const after = await page.request.get(`${API_BASE}/api/requisitions/${req.id}`, {
				headers: await authedTenantHeaders(page)
			});
			expect(((await after.json()) as Requisition).status).toBe('draft');
		} finally {
			deleteRequisition(req.id);
		}
	});

	test('a non-rejected requisition offers no Reopen action', async ({ page }) => {
		const req = await createRequisition(page, 'draft', await authedTenantHeaders(page));
		try {
			await page.goto('/requisitions?status=draft');
			const row = page.locator('table tbody tr', { hasText: req.requisition_number });
			await expect(row).toBeVisible();
			await expect(row.getByRole('button', { name: /^Reopen/ })).toHaveCount(0);
		} finally {
			deleteRequisition(req.id);
		}
	});
});

test.describe('/requisitions reopen (cfo — not in the gate)', () => {
	test.use({ storageState: { cookies: [], origins: [] } });

	test('a cfo sees the rejected row but no Reopen, and the API refuses them', async ({
		page,
		tenantAdmin,
		tenantCfo
	}) => {
		const slug = currentTenantSlug();
		// Seed as admin via a direct login call — no UI, no storage state.
		const login = await page.request.post(`${API_BASE}/api/auth/login`, {
			headers: { 'X-Tenant-Slug': slug },
			data: { email: tenantAdmin.email, password: tenantAdmin.password }
		});
		expect(login.status(), 'admin login for seeding').toBe(200);
		const adminToken = ((await login.json()) as { access_token: string }).access_token;
		const adminH = { Authorization: `Bearer ${adminToken}`, 'X-Tenant-Slug': slug };

		const req = await createRejectedRequisition(page, 'cfo', adminH);
		try {
			await signInAndWait(page, tenantCfo);
			await page.goto('/requisitions?status=rejected');

			const row = page.locator('table tbody tr', { hasText: req.requisition_number });
			await expect(row).toBeVisible();
			await expect(row.getByRole('button', { name: /^Reopen/ })).toHaveCount(0);

			// The UI is not the gate — the route refuses the cfo too.
			const refused = await page.request.post(`${API_BASE}/api/requisitions/${req.id}/reopen`, {
				headers: await authedTenantHeaders(page),
				data: {}
			});
			expect(refused.status(), 'cfo reopen is refused').toBe(403);

			const after = await page.request.get(`${API_BASE}/api/requisitions/${req.id}`, {
				headers: await authedTenantHeaders(page)
			});
			expect(((await after.json()) as Requisition).status).toBe('rejected');
		} finally {
			deleteRequisition(req.id);
		}
	});
});
