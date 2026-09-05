import type { Page } from '@playwright/test';

import { API_BASE, authedTenantHeaders, expect, test } from '../fixtures/helpers';

/**
 * The supplier-chat @mention picker has a real member source — and the vendor
 * surface has none.
 *
 * The picker shipped with no source at all. `InvoiceModal` handed
 * `SupplierChatThread` a `members` prop filled from `adminStore.users`, a list
 * only `/admin` and `/workflows/[id]` ever load. Landing on `/invoices` — the
 * only route this modal is reachable from — left the dropdown permanently
 * empty; visiting `/admin` first silently made it work, so the feature behaved
 * differently depending on how you got there.
 *
 * `GET /api/invoices/chat/mentionable-users` is now the source: gated on
 * `get_current_user`, exactly what posting a mention requires, returning
 * `{id, full_name, is_active}` and nothing else. That "nothing else" is
 * load-bearing and asserted below — the dropdown used to render each
 * candidate's EMAIL under their name.
 *
 * Two things are checked because either alone would pass while the feature was
 * broken:
 *   1. the AP composer offers real, server-supplied colleagues, and
 *   2. `GET /api/admin/users` — admin-only, and full of exactly the PII this
 *      picker must not hold — is never called, the same assertion
 *      `approver-picker.spec.ts` makes about the approver `<select>`.
 *
 * And the mirror: the supplier portal must never be handed an employee roster.
 * That is defended in three independent places (`members={[]}` at the call
 * site, the `isAp` gate around the whole picker, and the backend's masked
 * portal response), so the vendor assertion is about the observable outcome —
 * no picker, and no request for one.
 */

const MENTIONABLE = '**/api/invoices/chat/mentionable-users';

async function openFirstInvoice(page: Page) {
	await page.goto('/invoices');
	await expect(page.locator('table tbody tr').first()).toBeVisible();
	await page.locator('table tbody tr').first().getByRole('button', { name: 'Edit' }).click();
	const modal = page.locator('div.modal[role="dialog"]');
	await expect(modal).toBeVisible();
	return modal;
}

test.describe('/invoices supplier chat — @mention picker (AP surface)', () => {
	test('the picker offers the colleagues the server returns, and no email', async ({ page }) => {
		// Stubbed rather than seeded: the assertion is about what the composer
		// does with the response, and a stub makes "these exact names, and
		// nothing else on screen" a statement instead of a guess about what the
		// shard's tenant happens to hold.
		await page.route(MENTIONABLE, async (route) => {
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify([
					{ id: 'mention-user-1', full_name: 'Dana Mentionable', is_active: true },
					{ id: 'mention-user-2', full_name: 'Ravi Mentionable', is_active: true },
					// Inactive: the endpoint excludes these, but if one ever
					// arrives the composer must drop it too — offering someone
					// who cannot be notified is the failure this feature has.
					{ id: 'mention-user-3', full_name: 'Gone Mentionable', is_active: false }
				])
			});
		});

		const modal = await openFirstInvoice(page);
		const chat = modal.locator('[data-testid="supplier-chat"]');
		await expect(chat).toBeVisible({ timeout: 10_000 });

		await chat.locator('[data-testid="chat-mention-input"]').click();

		const list = chat.locator('[data-testid="chat-mention-list"]');
		await expect(list).toBeVisible({ timeout: 10_000 });
		await expect(list.getByRole('button', { name: /Dana Mentionable/ })).toBeVisible();
		await expect(list.getByRole('button', { name: /Ravi Mentionable/ })).toBeVisible();
		await expect(list.getByRole('button', { name: /Gone Mentionable/ })).toHaveCount(0);

		// No address anywhere in the dropdown. The picker used to render one per
		// row, which is a directory of every colleague's email rebuilt inside a
		// chat composer.
		expect(await list.innerText()).not.toContain('@');

		// Typing narrows on the name — the only field the source carries.
		await chat.locator('[data-testid="chat-mention-input"]').fill('Ravi');
		await expect(list.getByRole('button', { name: /Ravi Mentionable/ })).toBeVisible();
		await expect(list.getByRole('button', { name: /Dana Mentionable/ })).toHaveCount(0);

		// Picking one chips it above the composer, ready to send.
		await list.getByRole('button', { name: /Ravi Mentionable/ }).click();
		await expect(chat.getByText('@Ravi Mentionable')).toBeVisible();
	});

	test('the modal never calls the admin directory for mention candidates', async ({ page }) => {
		// `GET /api/admin/users` is `require_permission(user.manage)` and returns
		// emails, roles and last-login. Sourcing a picker from it 403s every
		// non-admin AND hands an admin a payload the composer has no business
		// holding — the same mistake the approver picker made, guarded the same
		// way.
		const adminDirectoryCalls: string[] = [];
		await page.route('**/api/admin/users*', async (route) => {
			adminDirectoryCalls.push(route.request().url());
			await route.continue();
		});

		const modal = await openFirstInvoice(page);
		const chat = modal.locator('[data-testid="supplier-chat"]');
		await expect(chat).toBeVisible({ timeout: 10_000 });
		await chat.locator('[data-testid="chat-mention-input"]').click();

		expect(adminDirectoryCalls, 'the mention picker must not read the admin directory').toEqual(
			[]
		);
	});

	test('the live endpoint returns the PII-free shape the picker relies on', async ({ page }) => {
		// The stubbed test above cannot tell a working endpoint from a missing
		// one — that indistinguishability is how a quietly empty picker survived
		// in the first place. So ask the real API, unstubbed, for the shape.
		// The ROLE matrix (all four employee roles read it; a clerk is offered)
		// is `backend/tests/test_chat_mentionable_users.py`, which can mint a
		// token per role; this spec runs as the worker's admin.
		const res = await page.request.get(`${API_BASE}/api/invoices/chat/mentionable-users`, {
			headers: await authedTenantHeaders(page)
		});
		expect(res.ok(), await res.text()).toBeTruthy();
		const body = (await res.json()) as Array<Record<string, unknown>>;
		expect(Array.isArray(body)).toBeTruthy();
		expect(body.length).toBeGreaterThan(0);
		for (const entry of body) {
			expect(Object.keys(entry).sort()).toEqual(['full_name', 'id', 'is_active']);
		}
		expect(JSON.stringify(body)).not.toContain('@');
	});
});

test.describe('/portal supplier chat — the vendor surface gets no roster', () => {
	test.use({ storageState: { cookies: [], origins: [] } });

	test('no mention picker, and no request for one', async ({ page }) => {
		const rosterRequests: string[] = [];
		await page.route(MENTIONABLE, async (route) => {
			rosterRequests.push(route.request().url());
			await route.continue();
		});

		await page.goto('/portal/login');
		await page.waitForLoadState('networkidle');
		await page.locator('input[type="email"]').fill('supplier@portal.test');
		await page.locator('input[type="password"]').fill('demo');
		await page.locator('button[type="submit"]').click();
		// Sign-in lands on the portal home; this spec needs the invoice list.
		await expect(page).toHaveURL(/\/portal\/?$/, { timeout: 15_000 });
		await page.goto('/portal/invoices');
		await page.waitForLoadState('networkidle');

		const firstRow = page.locator('table tbody tr.clickable').first();
		await expect(firstRow).toBeVisible({ timeout: 10_000 });
		await firstRow.click();

		const chat = page.locator('[data-testid="supplier-chat"]');
		await expect(chat).toBeVisible({ timeout: 10_000 });
		// The composer is there — the supplier can post. What it must not have
		// is any way to enumerate the buyer's employees.
		await expect(chat.locator('[data-testid="chat-input"]')).toBeVisible();
		await expect(chat.locator('[data-testid="chat-mention-input"]')).toHaveCount(0);
		await expect(chat.locator('[data-testid="chat-mention-list"]')).toHaveCount(0);

		expect(rosterRequests, 'the portal must never fetch the employee roster').toEqual([]);
	});
});
