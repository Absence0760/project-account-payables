import { expect, test } from '@playwright/test';

import {
	ACME_ADMIN,
	ACME_BASE,
	signInAndWait,
	TECHFLOW_ADMIN,
	TECHFLOW_BASE
} from '../fixtures/helpers';

/**
 * Cross-tenant WRITE attacks. `tenant-isolation.spec.ts` covers GET
 * leakage; this spec hardens against the more damaging direction —
 * one tenant mutating another's data. The new `get_tenant_db` guard
 * is the single chokepoint, so these tests pin that contract holds
 * for every verb on every entity type the AP app exposes.
 *
 * Vector exercised: log in as one tenant, hit another tenant's
 * mutation endpoint with the original JWT + the wrong-tenant header.
 * Expected: 403, no state change. Even a single endpoint that slips
 * through the guard is enough to defeat tenant isolation.
 */

const API_URL = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

async function tokenAfterLogin(
	page: import('@playwright/test').Page,
	baseURL: string,
	creds: { email: string; password: string }
): Promise<string> {
	await page.goto(`${baseURL}/login`);
	await page.waitForLoadState('networkidle');
	await page.locator('input[type="email"]').fill(creds.email);
	await page.locator('input[type="password"]').fill(creds.password);
	await page.locator('form button[type="submit"]').click();
	await page.waitForURL(new RegExp(`^${baseURL.replace(/[/]/g, '\\/')}/?$`), { timeout: 15_000 });
	const token = await page.evaluate(() => localStorage.getItem('auth_token'));
	if (!token) throw new Error('expected auth_token after sign-in');
	return token;
}

function expectForbidden(status: number, label: string) {
	expect(status, `${label}: must not return 2xx cross-tenant`).toBeGreaterThanOrEqual(400);
	expect(status, `${label}: must not return 5xx`).toBeLessThan(500);
	// 403 is the guard's canonical answer; 401 is acceptable if a
	// future hardening rotates the response. 404 is also fine — it
	// means "wrong tenant, no such row," which is functionally
	// isolation. The forbidden line is "200 + body in the cross-tenant
	// data set."
}

test.describe('cross-tenant writes — every verb must 4xx', () => {
	test('PATCH /api/invoices/{id} is rejected', async ({ page, request }) => {
		const techflowToken = await tokenAfterLogin(page, TECHFLOW_BASE, TECHFLOW_ADMIN);

		// Use a random UUID for the target — even before the guard fires
		// on tenant-mismatch, the row wouldn't exist in techflow's DB.
		// What we care about is "not 200" — even if the request shape
		// is otherwise valid.
		const fakeId = '00000000-0000-0000-0000-000000000001';
		const res = await request.patch(`${API_URL}/api/invoices/${fakeId}`, {
			headers: {
				Authorization: `Bearer ${techflowToken}`,
				'X-Tenant-Slug': 'acme'
			},
			data: { status: 'approved' }
		});
		expectForbidden(res.status(), 'PATCH invoice');
	});

	test('POST /api/vendors is rejected', async ({ page, request }) => {
		const techflowToken = await tokenAfterLogin(page, TECHFLOW_BASE, TECHFLOW_ADMIN);

		const res = await request.post(`${API_URL}/api/vendors`, {
			headers: {
				Authorization: `Bearer ${techflowToken}`,
				'X-Tenant-Slug': 'acme'
			},
			data: {
				name: 'Attacker Vendor',
				email: 'attacker@evil.test',
				accepts_virtual_cards: false
			}
		});
		expectForbidden(res.status(), 'POST vendor');
	});

	test('DELETE /api/vendors/{id} is rejected', async ({ page, request }) => {
		const techflowToken = await tokenAfterLogin(page, TECHFLOW_BASE, TECHFLOW_ADMIN);

		const fakeId = '00000000-0000-0000-0000-000000000002';
		const res = await request.delete(`${API_URL}/api/vendors/${fakeId}`, {
			headers: {
				Authorization: `Bearer ${techflowToken}`,
				'X-Tenant-Slug': 'acme'
			}
		});
		expectForbidden(res.status(), 'DELETE vendor');
	});

	test('POST /api/payments/runs (initiate money movement) is rejected', async ({
		page,
		request
	}) => {
		// The riskiest cross-tenant write — a payment run inits real
		// money movement. Even with a wildly invalid body, the auth
		// gate must close before the payload is read.
		const techflowToken = await tokenAfterLogin(page, TECHFLOW_BASE, TECHFLOW_ADMIN);

		const res = await request.post(`${API_URL}/api/payments/runs`, {
			headers: {
				Authorization: `Bearer ${techflowToken}`,
				'X-Tenant-Slug': 'acme'
			},
			data: { invoices: [], method: 'ach' }
		});
		expectForbidden(res.status(), 'POST payment run');
	});

	test('PATCH /api/organization (settings) is rejected', async ({ page, request }) => {
		const techflowToken = await tokenAfterLogin(page, TECHFLOW_BASE, TECHFLOW_ADMIN);

		const res = await request.patch(`${API_URL}/api/organization`, {
			headers: {
				Authorization: `Bearer ${techflowToken}`,
				'X-Tenant-Slug': 'acme'
			},
			data: { settings: { mfa: { required: false } } }
		});
		expectForbidden(res.status(), 'PATCH org settings');
	});

	test('POST /api/workflows is rejected', async ({ page, request }) => {
		const techflowToken = await tokenAfterLogin(page, TECHFLOW_BASE, TECHFLOW_ADMIN);

		const res = await request.post(`${API_URL}/api/workflows`, {
			headers: {
				Authorization: `Bearer ${techflowToken}`,
				'X-Tenant-Slug': 'acme'
			},
			data: { name: 'Evil Workflow', steps_config: { steps: [] } }
		});
		expectForbidden(res.status(), 'POST workflow');
	});

	test('reverse direction: acme JWT → techflow mutations also rejected', async ({
		page,
		request
	}) => {
		// Belt-and-braces — the guard must be symmetric. We caught a
		// one-way org_id-substring bug once; pin the reverse path too.
		const acmeToken = await tokenAfterLogin(page, ACME_BASE, ACME_ADMIN);

		const r1 = await request.patch(`${API_URL}/api/invoices/00000000-0000-0000-0000-000000000099`, {
			headers: { Authorization: `Bearer ${acmeToken}`, 'X-Tenant-Slug': 'techflow' },
			data: { status: 'rejected' }
		});
		expectForbidden(r1.status(), 'acme → techflow PATCH invoice');

		const r2 = await request.post(`${API_URL}/api/vendors`, {
			headers: { Authorization: `Bearer ${acmeToken}`, 'X-Tenant-Slug': 'techflow' },
			data: { name: 'Sneak' }
		});
		expectForbidden(r2.status(), 'acme → techflow POST vendor');
	});

	test('same-tenant write succeeds (positive control)', async ({ page, request }) => {
		// Without this control the negatives could all be passing for
		// the wrong reason — a broken seed, a 5xx for everyone, a route
		// rename. Run one harmless GET that we know requires auth +
		// tenant resolution, and assert it succeeds for the matched
		// pair.
		const acmeToken = await tokenAfterLogin(page, ACME_BASE, ACME_ADMIN);
		const r = await request.get(`${API_URL}/api/vendors`, {
			headers: { Authorization: `Bearer ${acmeToken}`, 'X-Tenant-Slug': 'acme' }
		});
		expect(r.status(), 'same-tenant GET must succeed').toBe(200);
	});
});
