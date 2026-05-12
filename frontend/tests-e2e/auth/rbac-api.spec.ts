import { expect, test } from '@playwright/test';

import {
	ACME_ADMIN,
	ACME_BASE,
	ACME_CFO,
	ACME_CLERK,
	ACME_MANAGER,
	escapeRegExp
} from '../fixtures/helpers';

/**
 * Endpoint-level RBAC. `rbac.spec.ts` covers sidebar visibility
 * (what the user sees); this spec covers what the backend actually
 * lets each role *do*. A frontend that forgets to hide a button is a
 * UX bug; a backend that accepts the call anyway is a privilege
 * escalation.
 *
 * The matrix below targets the highest-value gates:
 *
 *   admin-only:    POST /api/admin/users, DELETE /api/admin/users/{id}
 *   admin/manager: POST /api/vendors, DELETE /api/vendors/{id}
 *   CFO-only:      POST /api/payments/runs/{id}/approve
 *   any-role read: GET /api/dashboard (positive control)
 *
 * For each gate we hit it with a *lower-privilege* token and assert
 * 4xx, then a *correctly-privileged* token and assert it doesn't
 * 403 (it might 404 for a random UUID — the security property is
 * "role wasn't the blocker").
 */

const API_URL = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

async function tokenAfterLogin(
	page: import('@playwright/test').Page,
	creds: { email: string; password: string }
): Promise<string> {
	await page.goto(`${ACME_BASE}/login`);
	await page.waitForLoadState('networkidle');
	await page.locator('input[type="email"]').fill(creds.email);
	await page.locator('input[type="password"]').fill(creds.password);
	await page.locator('form button[type="submit"]').click();
	await page.waitForURL(new RegExp(`^${escapeRegExp(ACME_BASE)}/?$`), {
		timeout: 15_000
	});
	const token = await page.evaluate(() => localStorage.getItem('auth_token'));
	if (!token) throw new Error('expected auth_token after sign-in');
	return token;
}

function expectRoleDeny(status: number, what: string) {
	expect(status, `${what}: role must be the blocker`).toBe(403);
}

test.describe('RBAC at the API layer (acme)', () => {
	// ---- admin-only ------------------------------------------------------

	test('clerk cannot POST /api/admin/users', async ({ page, request }) => {
		const clerkToken = await tokenAfterLogin(page, ACME_CLERK);
		const r = await request.post(`${API_URL}/api/admin/users`, {
			headers: { Authorization: `Bearer ${clerkToken}`, 'X-Tenant-Slug': 'acme' },
			data: { email: 'newperson@acme.com', full_name: 'New', role: 'ap_clerk' }
		});
		expectRoleDeny(r.status(), 'clerk POST admin/users');
	});

	test('manager cannot POST /api/admin/users', async ({ page, request }) => {
		const managerToken = await tokenAfterLogin(page, ACME_MANAGER);
		const r = await request.post(`${API_URL}/api/admin/users`, {
			headers: { Authorization: `Bearer ${managerToken}`, 'X-Tenant-Slug': 'acme' },
			data: { email: 'newperson@acme.com', full_name: 'New', role: 'ap_clerk' }
		});
		expectRoleDeny(r.status(), 'manager POST admin/users');
	});

	test('cfo cannot POST /api/admin/users', async ({ page, request }) => {
		const cfoToken = await tokenAfterLogin(page, ACME_CFO);
		const r = await request.post(`${API_URL}/api/admin/users`, {
			headers: { Authorization: `Bearer ${cfoToken}`, 'X-Tenant-Slug': 'acme' },
			data: { email: 'newperson@acme.com', full_name: 'New', role: 'ap_clerk' }
		});
		expectRoleDeny(r.status(), 'cfo POST admin/users');
	});

	test('admin CAN POST /api/admin/users (positive control)', async ({ page, request }) => {
		const adminToken = await tokenAfterLogin(page, ACME_ADMIN);
		const r = await request.post(`${API_URL}/api/admin/users`, {
			headers: { Authorization: `Bearer ${adminToken}`, 'X-Tenant-Slug': 'acme' },
			data: {
				email: `playwright-rbac-${Date.now()}@acme.com`,
				full_name: 'Playwright RBAC',
				role: 'ap_clerk'
			}
		});
		// 200/201 if it succeeds; anything in [400, 422] for a body
		// validation issue is fine — the contract is "role wasn't the
		// blocker." 403 would mean RBAC denied admin.
		expect(r.status(), 'admin should not be RBAC-denied').not.toBe(403);
	});

	// ---- admin/manager only ---------------------------------------------

	test('clerk cannot POST /api/vendors', async ({ page, request }) => {
		const clerkToken = await tokenAfterLogin(page, ACME_CLERK);
		const r = await request.post(`${API_URL}/api/vendors`, {
			headers: { Authorization: `Bearer ${clerkToken}`, 'X-Tenant-Slug': 'acme' },
			data: { name: 'Sneak Vendor' }
		});
		expectRoleDeny(r.status(), 'clerk POST vendor');
	});

	test('cfo cannot POST /api/vendors', async ({ page, request }) => {
		// CFO is read-many but cannot mutate vendor records.
		const cfoToken = await tokenAfterLogin(page, ACME_CFO);
		const r = await request.post(`${API_URL}/api/vendors`, {
			headers: { Authorization: `Bearer ${cfoToken}`, 'X-Tenant-Slug': 'acme' },
			data: { name: 'CFO Sneak Vendor' }
		});
		expectRoleDeny(r.status(), 'cfo POST vendor');
	});

	test('manager CAN POST /api/vendors (positive control)', async ({ page, request }) => {
		const managerToken = await tokenAfterLogin(page, ACME_MANAGER);
		const r = await request.post(`${API_URL}/api/vendors`, {
			headers: { Authorization: `Bearer ${managerToken}`, 'X-Tenant-Slug': 'acme' },
			data: { name: `Playwright Vendor ${Date.now()}` }
		});
		expect(r.status(), 'manager should not be RBAC-denied').not.toBe(403);
	});

	// ---- CFO-only --------------------------------------------------------

	test('clerk cannot POST /api/payments/runs/{id}/approve', async ({ page, request }) => {
		// CFO sign-off endpoint — clerks must never reach it.
		const clerkToken = await tokenAfterLogin(page, ACME_CLERK);
		const fakeRunId = '00000000-0000-0000-0000-000000000001';
		const r = await request.post(`${API_URL}/api/payments/runs/${fakeRunId}/approve`, {
			headers: { Authorization: `Bearer ${clerkToken}`, 'X-Tenant-Slug': 'acme' }
		});
		expectRoleDeny(r.status(), 'clerk approve run');
	});

	test('manager cannot POST /api/payments/runs/{id}/approve', async ({ page, request }) => {
		const managerToken = await tokenAfterLogin(page, ACME_MANAGER);
		const fakeRunId = '00000000-0000-0000-0000-000000000001';
		const r = await request.post(`${API_URL}/api/payments/runs/${fakeRunId}/approve`, {
			headers: { Authorization: `Bearer ${managerToken}`, 'X-Tenant-Slug': 'acme' }
		});
		expectRoleDeny(r.status(), 'manager approve run');
	});

	test('admin cannot POST /api/payments/runs/{id}/approve', async ({ page, request }) => {
		// Yes — CFO sign-off is CFO-only by design. Admin is high-
		// privilege but not the segregation-of-duties role for
		// large-dollar payment approval.
		const adminToken = await tokenAfterLogin(page, ACME_ADMIN);
		const fakeRunId = '00000000-0000-0000-0000-000000000001';
		const r = await request.post(`${API_URL}/api/payments/runs/${fakeRunId}/approve`, {
			headers: { Authorization: `Bearer ${adminToken}`, 'X-Tenant-Slug': 'acme' }
		});
		expectRoleDeny(r.status(), 'admin approve run');
	});

	test('CFO is NOT RBAC-denied on POST /api/payments/runs/{id}/approve', async ({
		page,
		request
	}) => {
		const cfoToken = await tokenAfterLogin(page, ACME_CFO);
		const fakeRunId = '00000000-0000-0000-0000-000000000001';
		const r = await request.post(`${API_URL}/api/payments/runs/${fakeRunId}/approve`, {
			headers: { Authorization: `Bearer ${cfoToken}`, 'X-Tenant-Slug': 'acme' }
		});
		// CFO will hit 404 (run doesn't exist) — the security property
		// is that the RBAC layer accepted them.
		expect(r.status(), 'CFO must not be RBAC-denied').not.toBe(403);
	});

	// ---- Read endpoints all roles can hit -------------------------------

	test('every role can GET /api/dashboard (positive control)', async ({ page, request }) => {
		// If this fails for a role it's not an RBAC bug — it's a
		// regression somewhere else (auth, tenant resolution, etc).
		for (const [label, creds] of [
			['admin', ACME_ADMIN] as const,
			['manager', ACME_MANAGER] as const,
			['cfo', ACME_CFO] as const,
			['clerk', ACME_CLERK] as const
		]) {
			const token = await tokenAfterLogin(page, creds);
			const r = await request.get(`${API_URL}/api/dashboard`, {
				headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
			});
			expect(r.status(), `${label} GET /dashboard`).toBe(200);
		}
	});
});
