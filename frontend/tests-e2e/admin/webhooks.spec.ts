import {
	API_BASE,
	authedTenantHeaders,
	currentTenantSlug,
	expect,
	signInAndWait,
	test
} from '../fixtures/helpers';

/**
 * /admin/webhooks — Outbound-webhook management + redelivery UI (admin only).
 *
 * Surfaces the existing backend endpoints (`backend/app/api/webhooks.py`):
 *  - POST   /api/webhooks                          → create (signing secret returned ONCE)
 *  - GET    /api/webhooks                          → list (prefix + metadata only)
 *  - PATCH  /api/webhooks/{id}                     → edit
 *  - DELETE /api/webhooks/{id}                     → delete (CASCADEs deliveries)
 *  - GET    /api/webhooks/deliveries               → delivery log (status filter, paginated)
 *  - POST   /api/webhooks/deliveries/{id}/redeliver → re-enqueue a failed/dead delivery (409 if delivered)
 *
 * The signing secret is shown once in a copy-able reveal and never echoed after.
 * Login model mirrors the suite: the per-worker storage state signs the admin
 * in (the only role the endpoints allow), so the page loads without a redirect.
 */

async function apiHeaders(page: import('@playwright/test').Page) {
	return {
		...(await authedTenantHeaders(page)),
		'Content-Type': 'application/json'
	};
}

interface SubscriptionResponse {
	id: string;
	name: string;
	target_url: string;
	event_types: string[];
	secret_prefix: string;
	active: boolean;
}

/** Best-effort cleanup: delete a subscription we created in a test. */
async function deleteSub(page: import('@playwright/test').Page, id: string) {
	const headers = await apiHeaders(page);
	await page.request.delete(`${API_BASE}/api/webhooks/${id}`, { headers });
}

async function createSub(
	page: import('@playwright/test').Page,
	name: string
): Promise<{ subscription: SubscriptionResponse; signing_secret: string }> {
	const headers = await apiHeaders(page);
	return (await (
		await page.request.post(`${API_BASE}/api/webhooks`, {
			headers,
			data: {
				name,
				target_url: 'https://example.com/webhooks/ap',
				event_types: ['invoice.approved']
			}
		})
	).json()) as { subscription: SubscriptionResponse; signing_secret: string };
}

test.describe('/admin/webhooks (admin)', () => {
	// Deterministic explicit sign-in (don't lean on the shared storage cache) so
	// the gated page is reliably authed before each test.
	test.use({ storageState: { cookies: [], origins: [] } });

	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
	});

	test('create shows the signing secret once + lists the new subscription', async ({ page }) => {
		await page.goto('/admin/webhooks');
		await expect(page.getByRole('heading', { name: 'Webhooks', exact: true })).toBeVisible();

		const name = `e2e-hook-${Date.now()}`;

		// Open create modal + submit.
		await page.getByRole('button', { name: '+ Create webhook' }).click();
		const createModal = page.getByRole('dialog', { name: 'Create webhook' });
		await expect(createModal).toBeVisible();
		await createModal.getByRole('textbox').first().fill(name);
		await createModal.locator('input[type="url"]').fill('https://example.com/webhooks/ap');
		// invoice.approved is checked by default — submit.
		await createModal.getByRole('button', { name: 'Create' }).click();

		// The one-time reveal modal shows the FULL signing secret, warns it's
		// shown once, and offers a Copy button.
		const reveal = page.getByRole('dialog', { name: 'Webhook created' });
		await expect(reveal).toBeVisible({ timeout: 10_000 });
		const minted = reveal.getByTestId('minted-secret');
		await expect(minted).toBeVisible();
		const secret = (await minted.textContent())?.trim() ?? '';
		// The secret is a real, long token — not just the stored prefix.
		expect(secret.length).toBeGreaterThan(16);
		await expect(reveal.getByText(/shown only once/i)).toBeVisible();
		await expect(reveal.getByRole('button', { name: 'Copy' })).toBeVisible();

		// Dismiss the reveal — the secret must be gone (never re-shown).
		await reveal.getByRole('button', { name: 'Done' }).click();
		await expect(reveal).toBeHidden();
		await expect(page.getByTestId('minted-secret')).toHaveCount(0);

		// The new subscription is listed with its name + Active status, but NOT
		// the full secret (only the prefix).
		const row = page.locator('tr', { hasText: name });
		await expect(row).toBeVisible();
		await expect(row.getByText('Active', { exact: true })).toBeVisible();
		await expect(page.getByText(secret)).toHaveCount(0);

		// Cleanup via the API (resolve the id from the list).
		const headers = await apiHeaders(page);
		const list = (await (
			await page.request.get(`${API_BASE}/api/webhooks`, { headers })
		).json()) as SubscriptionResponse[];
		const created = list.find((s) => s.name === name);
		if (created) await deleteSub(page, created.id);
	});

	test('delete removes the subscription (armed two-click)', async ({ page }) => {
		const name = `e2e-del-${Date.now()}`;
		const created = await createSub(page, name);
		const id = created.subscription.id;

		await page.goto('/admin/webhooks');
		const row = page.locator('tr', { hasText: name });
		await expect(row).toBeVisible();

		// Two-click armed delete: first click arms ("Confirm"), second commits.
		// `exact` so the "Edit <name>" row link (no overlap, but be safe) isn't matched.
		await row.getByRole('button', { name: 'Delete', exact: true }).click();
		await row.getByRole('button', { name: 'Confirm', exact: true }).click();

		// Row is gone from the list.
		await expect(page.locator('tr', { hasText: name })).toHaveCount(0, { timeout: 10_000 });

		// Server-side: the subscription is gone (a repeat DELETE 404s/no-ops).
		const headers = await apiHeaders(page);
		const after = (await (
			await page.request.get(`${API_BASE}/api/webhooks`, { headers })
		).json()) as SubscriptionResponse[];
		expect(after.find((s) => s.id === id)).toBeUndefined();
	});

	test('the deliveries status filter is URL-backed', async ({ page }) => {
		await page.goto('/admin/webhooks');
		await expect(page.getByRole('heading', { name: 'Deliveries', exact: true })).toBeVisible();

		// Click the "Failed" filter chip — the URL gains ?status=failed.
		await page.getByRole('button', { name: 'Failed', exact: true }).click();
		await expect(page).toHaveURL(/[?&]status=failed/);

		// Reload preserves the filter and the chip stays pressed.
		await page.reload();
		await expect(page).toHaveURL(/[?&]status=failed/);
		await expect(page.getByRole('button', { name: 'Failed', exact: true })).toHaveAttribute(
			'aria-pressed',
			'true'
		);

		// Back to "All" clears the param.
		await page.getByRole('button', { name: 'All', exact: true }).click();
		await expect(page).not.toHaveURL(/[?&]status=/);
	});

	test('redeliver re-queues a failed delivery', async ({ page }) => {
		const name = `e2e-redeliver-${Date.now()}`;
		const created = await createSub(page, name);
		const subId = created.subscription.id;

		// Seed a FAILED delivery directly in the control DB so a Redeliver action
		// is rendered. (Deliveries are normally produced by the dispatch sweep,
		// which is off in dev.)
		const eventId = `e2e-evt-${Date.now()}`;
		const headers = await apiHeaders(page);
		const orgId = (await (
			await page.request.get(`${API_BASE}/api/auth/me`, { headers })
		).json())?.organization_id as string | undefined;

		// Insert via the control DB (webhook tables are control-plane).
		const { execFileSync } = await import('node:child_process');
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
				'account_payables',
				'-c',
				`INSERT INTO webhook_deliveries
				   (id, subscription_id, organization_id, event_id, event_type, payload, status, attempt_count)
				 VALUES
				   (gen_random_uuid(), '${subId}', '${orgId}', '${eventId}', 'invoice.approved',
				    '{}'::jsonb, 'failed', 1);`
			],
			{ env: { ...process.env, PGPASSWORD: 'postgres' }, stdio: ['ignore', 'pipe', 'pipe'] }
		);

		await page.goto('/admin/webhooks?status=failed');
		const row = page.locator('tr', { hasText: eventId });
		await expect(row).toBeVisible({ timeout: 10_000 });
		await expect(row.getByText('failed')).toBeVisible();

		// Redeliver re-enqueues it (the target is example.com, so the inline
		// attempt fails again — but the action returns without crashing and the
		// list refreshes). A success toast confirms the re-queue went through.
		await row.getByRole('button', { name: /Redeliver/ }).click();
		await expect(page.getByText('Delivery re-queued')).toBeVisible({ timeout: 10_000 });

		await deleteSub(page, subId);
	});
});

test.describe('/admin/webhooks (clerk — not authorized)', () => {
	test.use({ storageState: { cookies: [], origins: [] } });

	test('ap_clerk is redirected away and the API 403s them', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);

		await page.goto('/admin/webhooks');
		// admin-only — the page waits for /me then bounces the clerk to root.
		await page.waitForURL(/^http:\/\/[^/]+:7777\/?$/, { timeout: 15_000 });
		await expect(page.getByRole('heading', { name: 'Webhooks', exact: true })).toHaveCount(0);

		// The API itself 403s a non-admin.
		const token = await page.evaluate(() => localStorage.getItem('auth_token'));
		const resp = await page.request.get(`${API_BASE}/api/webhooks`, {
			headers: {
				Authorization: `Bearer ${token}`,
				'X-Tenant-Slug': currentTenantSlug()
			}
		});
		expect(resp.status()).toBe(403);
	});
});
