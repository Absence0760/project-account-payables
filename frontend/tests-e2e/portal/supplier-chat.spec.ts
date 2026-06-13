import type { Page } from '@playwright/test';

import {
	API_BASE,
	currentTenantSlug,
	expect,
	test,
	tenantPsql,
} from '../fixtures/helpers';

/**
 * Embedded Supplier Chat — portal (vendor) surface.
 *
 * On /portal/invoices, clicking a row expands a per-row chat panel mounting
 * <SupplierChatThread surface="vendor">. The vendor posts via
 * POST /api/portal/invoices/{id}/chat; messages render in the same bubble UI
 * as the AP side, but the portal response is MASKED — an AP-authored message
 * carries `author_name` only, never an internal `users.id` (no mentions).
 *
 * Contract (§10, frontend):
 *   1. The vendor expands a row, posts a message, and it renders (right-
 *      aligned for the supplier's own role).
 *   2. An AP-authored message shows the AP author's name but no internal id.
 *
 * Auth model + seed shape mirror portal.spec.ts: one VendorUser per tenant
 * (`supplier@portal.test`, password "demo"), owning at least one invoice. The
 * spec opts out of the worker-admin storage state and drives the portal login.
 */

const PORTAL_EMAIL = 'supplier@portal.test';
const PORTAL_PASSWORD = 'demo';

test.use({ storageState: { cookies: [], origins: [] } });

async function portalSignIn(page: Page) {
	await page.goto('/portal/login');
	await page.waitForLoadState('networkidle');
	await page.locator('input[type="email"]').fill(PORTAL_EMAIL);
	await page.locator('input[type="password"]').fill(PORTAL_PASSWORD);
	await page.locator('button[type="submit"]').click();
	await expect(page).toHaveURL(/\/portal\/invoices/, { timeout: 15_000 });
}

test.describe('/portal supplier chat — vendor surface', () => {
	test('a vendor expands a row and posts a message that renders', async ({ page }) => {
		await portalSignIn(page);

		const firstRow = page.locator('table tbody tr.clickable').first();
		await expect(firstRow).toBeVisible({ timeout: 10_000 });
		await firstRow.click();

		const chat = page.locator('[data-testid="supplier-chat"]');
		await expect(chat).toBeVisible({ timeout: 10_000 });

		const body = `e2e vendor chat ${Date.now()}`;
		await chat.locator('[data-testid="chat-input"]').fill(body);
		await chat.locator('[data-testid="chat-send"]').click();

		// The supplier's own message renders right-aligned (the vendor surface's
		// own role is `supplier`).
		const msg = chat.locator('[data-testid="chat-msg"]', { hasText: body });
		await expect(msg).toBeVisible({ timeout: 10_000 });
		await expect(msg).toHaveAttribute('data-own', 'true');
		await expect(msg).toHaveAttribute('data-role', 'supplier');
	});

	test('an AP-authored message is shown by name only, with no internal id', async ({
		page,
		request,
		tenantAdmin,
	}) => {
		await portalSignIn(page);

		const slug = currentTenantSlug();

		// An invoice the portal vendor owns.
		const vendorId = tenantPsql(
			`SELECT vendor_id FROM vendor_users WHERE email='${PORTAL_EMAIL}'`,
		).trim();
		expect(vendorId).not.toEqual('');
		const invoiceId = tenantPsql(
			`SELECT id FROM invoices WHERE vendor_id='${vendorId}' ORDER BY created_at DESC LIMIT 1`,
		).trim();
		expect(invoiceId).not.toEqual('');

		// Author an AP-side (ap_team) message via the AP API so the portal has a
		// foreign message to mask. The AP response DOES expose author_user_id.
		const apToken = await apAdminToken(page, slug, tenantAdmin);
		const apBody = `ap reply ${Date.now()}`;
		const apPost = await request.post(`${API_BASE}/api/invoices/${invoiceId}/chat`, {
			headers: { Authorization: `Bearer ${apToken}`, 'X-Tenant-Slug': slug },
			data: { body: apBody },
		});
		expect(apPost.ok()).toBeTruthy();
		const apMsg = (await apPost.json()) as { author_user_id?: string };
		expect(apMsg.author_user_id).toBeTruthy();

		// Read the SAME thread through the portal client: the AP message must be
		// present, attributed by name, with NO author_user_id (the mask).
		const portalToken = await page.evaluate(() => localStorage.getItem('portal_auth_token'));
		const res = await request.get(`${API_BASE}/api/portal/invoices/${invoiceId}/chat`, {
			headers: { Authorization: `Bearer ${portalToken}`, 'X-Tenant-Slug': slug },
		});
		expect(res.ok()).toBeTruthy();
		const thread = (await res.json()) as { messages: Array<Record<string, unknown>> };
		const apSeen = thread.messages.find((m) => m.body === apBody);
		expect(apSeen).toBeTruthy();
		expect(apSeen!.author_role).toEqual('ap_team');
		expect(apSeen!.author_name).toBeTruthy();
		expect(apSeen).not.toHaveProperty('author_user_id');
		expect(apSeen).not.toHaveProperty('mention_user_ids');

		// And in the rendered panel, the AP message shows its author name.
		await page.reload();
		await page.waitForLoadState('networkidle');
		// Find the row whose expanded panel carries the AP message. The portal
		// list is small; expand the first row that owns this thread.
		const row = page.locator('table tbody tr.clickable').first();
		await row.click();
		const chat = page.locator('[data-testid="supplier-chat"]');
		await expect(chat).toBeVisible({ timeout: 10_000 });

		// Locate the specific AP-authored bubble (by its body text) and assert it
		// is attributed by a non-empty author name. This is the contract check:
		// the portal must mask the internal id while still showing who posted —
		// catching a regression where the portal masks the body instead of the id.
		const apBubble = chat.locator('[data-testid="chat-msg"]', { hasText: apBody });
		await expect(apBubble).toBeVisible({ timeout: 10_000 });
		await expect(apBubble).toHaveAttribute('data-role', 'ap_team');
		await expect(apBubble.locator('.chat-author')).not.toBeEmpty();
	});
});

/** Mint a control-plane admin bearer token via the AP login API. The AP app
 *  stores its JWT under `auth_token`, a different key from the portal's
 *  `portal_auth_token`, so this request-context login does not disturb the
 *  portal session in localStorage (we go through `page.request`, not the UI). */
async function apAdminToken(
	page: Page,
	slug: string,
	creds: { email: string; password: string },
): Promise<string> {
	const res = await page.request.post(`${API_BASE}/api/auth/login`, {
		headers: { 'X-Tenant-Slug': slug },
		data: { email: creds.email, password: creds.password },
	});
	if (!res.ok()) throw new Error(`AP admin login failed: ${res.status()}`);
	const body = (await res.json()) as { access_token: string };
	return body.access_token;
}
