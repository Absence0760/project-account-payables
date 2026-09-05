import {
	API_BASE,
	authedTenantHeaders,
	currentTenantSlug,
	expect,
	signInAndWait,
	test
} from '../fixtures/helpers';

/**
 * `/intake` — the rework loop for a REJECTED intake request.
 *
 * `POST /api/intake/{id}/reopen` (`rejected -> open`) has always existed and is
 * in the documented lifecycle, but nothing in the product ever called it. From
 * `rejected` every other route 422s — `submit` wants `open`, `cancel` isn't
 * reachable from `rejected`, `PATCH` is open-only — so a rejected intake was
 * stranded on screen with DELETE as its only exit: the request number, the
 * reviewer's `form_data.review_reason` and the audit link between attempts all
 * lost on a re-key.
 *
 * Two things this locks besides "the button exists":
 *   - the armed confirm names the DESTINATION (`Reopen to Open`), because the
 *     sibling /requisitions reopen lands in `Draft` instead and the user has to
 *     know which side of the review line they are back on;
 *   - the role gate is the BROAD set. `require_roles(ADMIN, AP_MANAGER,
 *     AP_CLERK, CFO)` on the route — deliberately wider than approve/reject —
 *     so the clerk who has to redo the ask can reach it. Gating the button on
 *     the page's `canReview` (admin | ap_manager) would have re-stranded them.
 */

type Intake = { id: string; request_number: string; status: string };

async function createRejectedIntake(
	page: import('@playwright/test').Page,
	tag: string,
	headers?: Record<string, string>
): Promise<Intake> {
	const h = headers ?? (await authedTenantHeaders(page));
	const request_number = `IN-REOPEN-${tag}-${Date.now()}`;
	const created = await page.request.post(`${API_BASE}/api/intake`, {
		headers: h,
		data: { request_number, title: 'Rework-loop fixture', currency: 'USD' }
	});
	expect(created.status(), 'create intake').toBe(201);
	const intake = (await created.json()) as Intake;

	const submitted = await page.request.post(`${API_BASE}/api/intake/${intake.id}/submit`, {
		headers: h,
		data: {}
	});
	expect(submitted.status(), 'submit intake').toBe(200);

	const rejected = await page.request.post(`${API_BASE}/api/intake/${intake.id}/reject`, {
		headers: h,
		data: { reason: 'Needs a second quote' }
	});
	expect(rejected.status(), 'reject intake').toBe(200);
	expect(((await rejected.json()) as Intake).status).toBe('rejected');

	return { ...intake, status: 'rejected' };
}

async function createOpenIntake(page: import('@playwright/test').Page): Promise<Intake> {
	const request_number = `IN-NOREOPEN-${Date.now()}`;
	const created = await page.request.post(`${API_BASE}/api/intake`, {
		headers: await authedTenantHeaders(page),
		data: { request_number, title: 'Not rejected', currency: 'USD' }
	});
	expect(created.status(), 'create intake').toBe(201);
	return (await created.json()) as Intake;
}

async function deleteIntake(
	page: import('@playwright/test').Page,
	id: string,
	headers?: Record<string, string>
) {
	const h = headers ?? (await authedTenantHeaders(page).catch(() => null));
	if (!h) return;
	await page.request.delete(`${API_BASE}/api/intake/${id}`, { headers: h }).catch(() => {});
}

test.describe('/intake reopen (rework loop)', () => {
	test('a rejected intake reopens to Open from the row', async ({ page }) => {
		const intake = await createRejectedIntake(page, 'ui');
		try {
			await page.goto('/intake?status=rejected');

			const row = page.locator('table tbody tr', { hasText: intake.request_number });
			await expect(row).toBeVisible();
			await expect(row.locator('.badge.rejected')).toBeVisible();

			// One click arms; the armed copy names where the row lands.
			const reopen = row.getByRole('button', { name: 'Reopen', exact: true });
			await expect(reopen).toBeVisible();
			await reopen.click();
			await expect(row.getByRole('button', { name: 'Reopen to Open' })).toBeVisible();

			// Second click commits.
			await row.getByRole('button', { name: 'Reopen to Open' }).click();

			// The row is patched in place (no refetch), so it stays on screen and
			// flips to `open` — and the normal path is offered again, which is
			// exactly what the strand denied.
			await expect(row.locator('.badge.open')).toBeVisible();
			await expect(row.getByRole('button', { name: 'Submit' })).toBeVisible();
			await expect(row.getByRole('button', { name: /^Reopen/ })).toHaveCount(0);

			// The server agrees — the transition is `rejected -> open`, persisted.
			const after = await page.request.get(`${API_BASE}/api/intake/${intake.id}`, {
				headers: await authedTenantHeaders(page)
			});
			expect(((await after.json()) as Intake).status).toBe('open');
		} finally {
			await deleteIntake(page, intake.id);
		}
	});

	test('a non-rejected intake offers no Reopen action', async ({ page }) => {
		const intake = await createOpenIntake(page);
		try {
			await page.goto('/intake?status=open');
			const row = page.locator('table tbody tr', { hasText: intake.request_number });
			await expect(row).toBeVisible();
			await expect(row.getByRole('button', { name: /^Reopen/ })).toHaveCount(0);
		} finally {
			await deleteIntake(page, intake.id);
		}
	});
});

test.describe('/intake reopen (role gate is the broad set)', () => {
	// Every authenticated role the tenant has (admin / ap_manager / ap_clerk /
	// cfo) may reopen — that IS the backend gate, so there is no role here that
	// must be refused. What can regress is the opposite mistake: gating the
	// button on the reviewer predicate, which hides the rework loop from the
	// clerk whose request it is. This pins the clerk case.
	test.use({ storageState: { cookies: [], origins: [] } });

	test('an ap_clerk sees Reopen on a rejected intake', async ({
		page,
		tenantAdmin,
		tenantClerk
	}) => {
		const slug = currentTenantSlug();
		// Seed as admin (reject is admin | ap_manager), via a direct login call —
		// no UI, no storage state.
		const login = await page.request.post(`${API_BASE}/api/auth/login`, {
			headers: { 'X-Tenant-Slug': slug },
			data: { email: tenantAdmin.email, password: tenantAdmin.password }
		});
		expect(login.status(), 'admin login for seeding').toBe(200);
		const adminToken = ((await login.json()) as { access_token: string }).access_token;
		const adminH = { Authorization: `Bearer ${adminToken}`, 'X-Tenant-Slug': slug };

		const intake = await createRejectedIntake(page, 'clerk', adminH);
		try {
			await signInAndWait(page, tenantClerk);
			await page.goto('/intake?status=rejected');

			const row = page.locator('table tbody tr', { hasText: intake.request_number });
			await expect(row).toBeVisible();
			await expect(row.getByRole('button', { name: 'Reopen', exact: true })).toBeVisible();
			// …and the reviewer-only actions stay hidden for the same row.
			await expect(row.getByRole('button', { name: 'Approve' })).toHaveCount(0);
		} finally {
			await deleteIntake(page, intake.id, adminH);
		}
	});
});
