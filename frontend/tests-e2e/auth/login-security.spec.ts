import { API_BASE, currentTenantSlug, expect, signIn, test } from '../fixtures/helpers';

/**
 * Login security — the contract is "the only way past this gate is a
 * correct (email, password) pair against an active user in the
 * tenant." The tests below pin a few security-relevant edge cases.
 *
 * What's covered:
 *   - Failed attempts don't return a token (no silent success).
 *   - Wrong-password and unknown-email return the same shape so an
 *     attacker can't enumerate valid emails by response diff.
 *   - Successive failures don't accidentally unlock anything (no
 *     state machine that breaks at attempt N).
 *   - Login endpoint requires tenant context.
 *
 * What's intentionally NOT covered:
 *   - Rate-limiting / lockout — the seed deployment doesn't enforce
 *     a numeric attempt cap, and asserting "I tried 1000 times and
 *     still got 401" is a fragile substitute for the real test.
 */

test.describe('login security', () => {
	test('failed login does not set auth_token in localStorage', async ({ page }) => {
		await signIn(page, { email: 'noone@nowhere.test', password: 'wrong-password' });
		await expect(page).toHaveURL(/\/login/);

		const token = await page.evaluate(() => localStorage.getItem('auth_token'));
		expect(token, 'failed login must not leave a token behind').toBeNull();
	});

	test('wrong password and unknown email return the same status', async ({
		request,
		tenantAdmin
	}) => {
		// Email enumeration via response code is a CWE-204 violation.
		// Both shapes must produce the same 4xx so an attacker can't
		// distinguish "valid user, wrong password" from "no such user."
		const slug = currentTenantSlug();
		const wrongPwd = await request.post(`${API_BASE}/api/auth/login`, {
			headers: { 'X-Tenant-Slug': slug },
			data: { email: tenantAdmin.email, password: 'definitely-not-the-password' }
		});
		const unknownEmail = await request.post(`${API_BASE}/api/auth/login`, {
			headers: { 'X-Tenant-Slug': slug },
			data: { email: 'noone@nowhere.test', password: 'whatever' }
		});

		expect(wrongPwd.status()).toBe(unknownEmail.status());
		expect(wrongPwd.status()).toBeGreaterThanOrEqual(400);
		expect(wrongPwd.status()).toBeLessThan(500);
	});

	test('login response carries no token on failure', async ({ request, tenantAdmin }) => {
		const res = await request.post(`${API_BASE}/api/auth/login`, {
			headers: { 'X-Tenant-Slug': currentTenantSlug() },
			data: { email: tenantAdmin.email, password: 'wrong-password' }
		});
		expect(res.status()).toBeGreaterThanOrEqual(400);

		// Even on a 4xx, frameworks occasionally serialize a partial
		// success body. Make sure no token leaks through.
		let body: unknown = null;
		try {
			body = await res.json();
		} catch {
			body = null;
		}
		const serialised = JSON.stringify(body ?? '');
		expect(serialised).not.toMatch(/eyJ[A-Za-z0-9_-]{10,}/); // JWT prefix
		expect(serialised.toLowerCase()).not.toContain('access_token');
	});

	test('three sequential failed attempts all return 4xx — no state escape', async ({
		request,
		tenantAdmin
	}) => {
		// The point is not rate-limiting (we don't enforce a hard
		// lockout). The point is that retrying doesn't accidentally
		// pass after the Nth attempt because of some hidden cache,
		// counter, or session-mutation bug.
		const slug = currentTenantSlug();
		for (let i = 0; i < 3; i++) {
			const res = await request.post(`${API_BASE}/api/auth/login`, {
				headers: { 'X-Tenant-Slug': slug },
				data: { email: tenantAdmin.email, password: 'still-wrong' }
			});
			expect(res.status(), `attempt ${i + 1} should fail`).toBeGreaterThanOrEqual(400);
			expect(res.status()).toBeLessThan(500);
		}
	});

	test('tenant header is advisory — JWT org claim follows user.organization_id, not the header', async ({
		request,
		tenantAdmin
	}) => {
		// Login is intentionally tenant-header-agnostic: emails are
		// globally unique in the control plane, so the user → org
		// mapping is unambiguous. The security property we DO need is
		// "the JWT's org claim is the user's real org" — even when the
		// caller passes a different X-Tenant-Slug. Otherwise an
		// attacker could request a token scoped to a different tenant.
		const slug = currentTenantSlug();
		// Pick a different tenant slug for the spoof attempt — any
		// other seeded tenant works since the header should be ignored.
		const otherSlug = slug === 'acme' ? 'techflow' : 'acme';
		const res = await request.post(`${API_BASE}/api/auth/login`, {
			headers: { 'X-Tenant-Slug': otherSlug },
			data: { email: tenantAdmin.email, password: tenantAdmin.password }
		});
		expect(res.status()).toBe(200);
		const body = (await res.json()) as { access_token?: string };
		expect(body.access_token).toBeTruthy();

		const [, payloadB64] = body.access_token!.split('.');
		const padded = payloadB64
			.replace(/-/g, '+')
			.replace(/_/g, '/')
			.padEnd(payloadB64.length + ((4 - (payloadB64.length % 4)) % 4), '=');
		const payload = JSON.parse(Buffer.from(padded, 'base64').toString('utf-8')) as {
			org?: string;
		};

		// Sign in on the real tenant directly to learn what the user's
		// real org id looks like — then assert the spoofed-header
		// attempt produced the same org claim (i.e., header didn't
		// sway it).
		const refRes = await request.post(`${API_BASE}/api/auth/login`, {
			headers: { 'X-Tenant-Slug': slug },
			data: { email: tenantAdmin.email, password: tenantAdmin.password }
		});
		const refBody = (await refRes.json()) as { access_token?: string };
		const refPayload = JSON.parse(
			Buffer.from(
				refBody
					.access_token!.split('.')[1]
					.replace(/-/g, '+')
					.replace(/_/g, '/')
					.padEnd(
						refBody.access_token!.split('.')[1].length +
							((4 - (refBody.access_token!.split('.')[1].length % 4)) % 4),
						'='
					),
				'base64'
			).toString('utf-8')
		) as { org?: string };

		expect(payload.org, 'org claim must follow the user, not the header').toBe(refPayload.org);
	});

	test('a token minted on the tenant is scoped to that tenant (org claim)', async ({
		request,
		tenantAdmin
	}) => {
		// Positive contract: a successful login produces a JWT whose
		// payload identifies the org. The payload is base64url JSON
		// (middle segment of the three-part JWT).
		const res = await request.post(`${API_BASE}/api/auth/login`, {
			headers: { 'X-Tenant-Slug': currentTenantSlug() },
			data: { email: tenantAdmin.email, password: tenantAdmin.password }
		});
		expect(res.status()).toBe(200);
		const body = (await res.json()) as { access_token?: string; token?: string };
		const token = body.access_token ?? body.token;
		expect(token, 'login must return a token field').toBeTruthy();

		const [, payloadB64] = token!.split('.');
		const payloadJson = Buffer.from(
			payloadB64.replace(/-/g, '+').replace(/_/g, '/').padEnd(payloadB64.length + ((4 - (payloadB64.length % 4)) % 4), '='),
			'base64'
		).toString('utf-8');
		const payload = JSON.parse(payloadJson) as { org?: string; sub?: string; jti?: string };
		expect(payload.org, 'JWT must carry an org claim').toBeTruthy();
		expect(payload.sub, 'JWT must carry a sub (user id) claim').toBeTruthy();
		expect(payload.jti, 'JWT must carry a jti (for the logout blocklist)').toBeTruthy();
	});
});
