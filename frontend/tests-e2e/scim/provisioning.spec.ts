import { API_BASE, authedTenantHeaders, expect, test } from '../fixtures/helpers';
import type { Page } from '@playwright/test';

/**
 * SCIM 2.0 provisioning — the server-side contract an IdP drives.
 *
 * Our app is the SCIM *Service Provider*: an IdP (Okta / Entra / Authentik)
 * pushes users into `/api/scim/v2/Users` with a per-tenant bearer token. This
 * spec drives that endpoint with the exact request sequence Authentik sends
 * during outbound provisioning, then verifies the effect surfaces in the
 * `/admin` user list:
 *
 *   POST   /Users                     → user created, appears Active in /admin
 *   GET    /Users?filter=userName eq  → lookup-by-username (Authentik's dedupe)
 *   PATCH  /Users/{id} active=false   → deprovision, appears Inactive in /admin
 *   DELETE /Users/{id}                → soft-delete (stays Inactive)
 *
 * It is deterministic and needs nothing beyond the seeded stack — the same
 * coverage the live Authentik container exercises by the same protocol, minus
 * the container. See docs/local-sso-keycloak.md § Authentik for the live path.
 *
 * The bearer token is minted through the real admin endpoint
 * (`POST /api/organization/sso/scim-token`), so this also covers token mint →
 * SCIM auth resolution end to end.
 */

const SCIM = `${API_BASE}/api/scim/v2`;
const USER_SCHEMA = 'urn:ietf:params:scim:schemas:core:2.0:User';
const PATCH_OP_SCHEMA = 'urn:ietf:params:scim:api:messages:2.0:PatchOp';

/** Mint a fresh SCIM bearer token for the worker's tenant via the admin API,
 *  then return the Authorization header an IdP would send. */
async function scimAuth(page: Page): Promise<{ Authorization: string }> {
	const resp = await page.request.post(`${API_BASE}/api/organization/sso/scim-token`, {
		headers: await authedTenantHeaders(page)
	});
	expect(resp.status()).toBe(200);
	const { token } = (await resp.json()) as { token: string };
	return { Authorization: `Bearer ${token}` };
}

/** POST a SCIM user the way Authentik shapes the payload. Returns the new id. */
async function scimCreate(
	page: Page,
	auth: { Authorization: string },
	email: string,
	externalId: string
): Promise<string> {
	const resp = await page.request.post(`${SCIM}/Users`, {
		headers: auth,
		data: {
			schemas: [USER_SCHEMA],
			userName: email,
			externalId,
			name: { givenName: 'Scim', familyName: 'Provisioned', formatted: 'Scim Provisioned' },
			emails: [{ value: email, primary: true, type: 'work' }],
			active: true
		}
	});
	expect(resp.status()).toBe(201);
	const body = (await resp.json()) as { id: string; userName: string; active: boolean };
	expect(body.userName).toBe(email);
	expect(body.active).toBe(true);
	return body.id;
}

async function adminDelete(page: Page, id: string): Promise<void> {
	await page.request.delete(`${API_BASE}/api/admin/users/${id}`, {
		headers: await authedTenantHeaders(page)
	});
}

test.describe('SCIM provisioning (IdP → /api/scim/v2)', () => {
	test('ServiceProviderConfig advertises PATCH + filter so the IdP proceeds', async ({ page }) => {
		const resp = await page.request.get(`${SCIM}/ServiceProviderConfig`, { headers: await scimAuth(page) });
		expect(resp.status()).toBe(200);
		const cfg = (await resp.json()) as {
			patch: { supported: boolean };
			filter: { supported: boolean };
		};
		expect(cfg.patch.supported).toBe(true);
		expect(cfg.filter.supported).toBe(true);
	});

	test('rejects a request with no / bad bearer token (401, SCIM error shape)', async ({ page }) => {
		const noAuth = await page.request.get(`${SCIM}/Users`);
		expect(noAuth.status()).toBe(401);

		const badAuth = await page.request.get(`${SCIM}/Users`, {
			headers: { Authorization: 'Bearer not-a-real-token' }
		});
		expect(badAuth.status()).toBe(401);
		const body = (await badAuth.json()) as { detail: { status: string } };
		// SCIM-flavoured error envelope, not FastAPI's bare {detail: "..."}.
		expect(body.detail.status).toBe('401');
	});

	test('POST /Users provisions a user that appears Active in /admin', async ({ page }) => {
		const auth = await scimAuth(page);
		const stamp = Date.now();
		const email = `scim-create-${stamp}@scim-test.example`;
		let id: string | null = null;

		try {
			id = await scimCreate(page, auth, email, `ext-${stamp}`);

			// Lookup-by-userName — Authentik does this to avoid double-creating.
			const list = await page.request.get(`${SCIM}/Users`, {
				headers: auth,
				params: { filter: `userName eq "${email}"` }
			});
			expect(list.status()).toBe(200);
			const listBody = (await list.json()) as {
				totalResults: number;
				Resources: Array<{ id: string }>;
			};
			expect(listBody.totalResults).toBe(1);
			expect(listBody.Resources[0].id).toBe(id);

			// The provisioned user is a real org member: it shows up in /admin.
			await page.goto('/admin');
			await page.getByPlaceholder('Search name or email...').fill(email);
			const row = page.locator('table tbody tr', { hasText: email });
			await expect(row).toBeVisible();
			await expect(row.locator('.status-dot')).toContainText('Active');
			// SCIM-provisioned users carry no role until an admin grants one.
			await expect(row.locator('.no-roles')).toBeVisible();
		} finally {
			if (id) await adminDelete(page, id);
		}
	});

	test('PUT /Users/{id} replaces the resource (Authentik update path)', async ({ page }) => {
		const auth = await scimAuth(page);
		const stamp = Date.now();
		const email = `scim-put-${stamp}@scim-test.example`;
		let id: string | null = null;

		try {
			id = await scimCreate(page, auth, email, `ext-${stamp}`);

			// Authentik updates a user by PUTting the whole resource (not PATCH).
			const put = await page.request.put(`${SCIM}/Users/${id}`, {
				headers: auth,
				data: {
					schemas: [USER_SCHEMA],
					userName: email,
					externalId: `ext-${stamp}`,
					name: { givenName: 'Renamed', familyName: 'ViaPut', formatted: 'Renamed ViaPut' },
					emails: [{ value: email, primary: true, type: 'work' }],
					active: true
				}
			});
			expect(put.status()).toBe(200);
			expect(((await put.json()) as { id: string }).id).toBe(id);

			await page.goto('/admin');
			await page.getByPlaceholder('Search name or email...').fill(email);
			const row = page.locator('table tbody tr', { hasText: email });
			await expect(row).toBeVisible();
			await expect(row.locator('.name-cell')).toContainText('Renamed ViaPut');
		} finally {
			if (id) await adminDelete(page, id);
		}
	});

	test('PATCH active=false then DELETE deprovisions — /admin shows Inactive', async ({ page }) => {
		const auth = await scimAuth(page);
		const stamp = Date.now();
		const email = `scim-deprov-${stamp}@scim-test.example`;
		let id: string | null = null;

		try {
			id = await scimCreate(page, auth, email, `ext-${stamp}`);

			// Deprovision: Authentik PATCHes active=false on unassign.
			const patch = await page.request.patch(`${SCIM}/Users/${id}`, {
				headers: auth,
				data: {
					schemas: [PATCH_OP_SCHEMA],
					Operations: [{ op: 'replace', path: 'active', value: false }]
				}
			});
			expect(patch.status()).toBe(200);
			expect(((await patch.json()) as { active: boolean }).active).toBe(false);

			await page.goto('/admin');
			await page.getByPlaceholder('Search name or email...').fill(email);
			const row = page.locator('table tbody tr', { hasText: email });
			await expect(row.locator('.status-dot')).toContainText('Inactive');
			await expect(row).toHaveClass(/inactive/);

			// SCIM DELETE is a soft-delete (preserves the audit trail) — the
			// user stays present + Inactive, not hard-removed.
			const del = await page.request.delete(`${SCIM}/Users/${id}`, { headers: auth });
			expect(del.status()).toBe(204);

			await page.reload();
			await page.getByPlaceholder('Search name or email...').fill(email);
			await expect(page.locator('table tbody tr', { hasText: email }).locator('.status-dot')).toContainText(
				'Inactive'
			);
		} finally {
			if (id) await adminDelete(page, id);
		}
	});
});
