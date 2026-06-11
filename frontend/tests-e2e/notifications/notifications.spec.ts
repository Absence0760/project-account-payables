import {
	API_BASE,
	authedTenantHeaders,
	expect,
	signInAndWait,
	tenantPsql,
	test
} from '../fixtures/helpers';

type Page = import('@playwright/test').Page;

async function currentUserId(page: Page): Promise<string> {
	const headers = await authedTenantHeaders(page);
	const resp = await page.request.get(`${API_BASE}/api/auth/me`, { headers });
	const body = (await resp.json()) as { id: string };
	return body.id;
}

/** The current worker tenant's organization_id — pulled off any seeded row.
 *  Invoices always exist in the seed, and carry organization_id. */
function orgId(): string {
	const out = tenantPsql('SELECT organization_id FROM invoices LIMIT 1');
	const id = out
		.split('\n')
		.map((l) => l.trim())
		.find((l) => /^[0-9a-f-]{36}$/i.test(l));
	if (!id) throw new Error('no seeded invoice to borrow organization_id from');
	return id;
}

/** Insert `n` unread notifications addressed to `recipient` directly into the
 *  worker's tenant DB and return their ids. Self-seeding so the spec doesn't
 *  depend on app state that may not have fired a real event yet. */
function seedNotifications(
	recipient: string,
	org: string,
	n: number,
	opts: { entityId?: string; title?: string } = {}
): string[] {
	const entity = opts.entityId
		? `'${opts.entityId}'::uuid`
		: '(SELECT id FROM invoices LIMIT 1)';
	const title = opts.title ?? 'E2E notification';
	const out = tenantPsql(
		`INSERT INTO notifications
		   (id, correlation_id, organization_id, recipient_user_id, event_type,
		    entity_type, entity_id, title, body, read_at)
		 SELECT gen_random_uuid(), gen_random_uuid(), '${org}'::uuid, '${recipient}'::uuid,
		        'invoice_approved', 'invoice', ${entity}, '${title}', 'seeded body', NULL
		 FROM generate_series(1, ${n})
		 RETURNING id`
	);
	const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
	return out
		.split('\n')
		.map((l) => l.trim())
		.filter((l) => UUID.test(l));
}

function clearNotifications(recipient: string) {
	tenantPsql(`DELETE FROM notifications WHERE recipient_user_id = '${recipient}'::uuid`);
}

test.describe('notification center', () => {
	test('badge shows unread count and the center lists notifications', async ({ page }) => {
		await signInAndWait(page);
		const me = await currentUserId(page);
		const org = orgId();
		clearNotifications(me);
		seedNotifications(me, org, 3);

		await page.goto('/notifications');
		// The center lists the seeded rows.
		await expect(page.getByText('E2E notification').first()).toBeVisible();

		// Sidebar badge reflects unread count (poll fires on mount).
		const badge = page.locator('.nav-badge');
		await expect(badge).toHaveText('3', { timeout: 15_000 });

		clearNotifications(me);
	});

	test('mark one read decrements the badge and updates the row', async ({ page }) => {
		await signInAndWait(page);
		const me = await currentUserId(page);
		const org = orgId();
		clearNotifications(me);
		seedNotifications(me, org, 2);

		await page.goto('/notifications');
		await expect(page.locator('.nav-badge')).toHaveText('2', { timeout: 15_000 });

		// Open the first notification's RowLink — marks read, then navigates to the invoice.
		await page.getByRole('button', { name: /^Open E2E notification/ }).first().click();
		await expect(page).toHaveURL(/\/invoices\?id=/);

		// Back on the center, badge is now 1.
		await page.goto('/notifications');
		await expect(page.locator('.nav-badge')).toHaveText('1', { timeout: 15_000 });

		clearNotifications(me);
	});

	test('mark all read clears the badge', async ({ page }) => {
		await signInAndWait(page);
		const me = await currentUserId(page);
		const org = orgId();
		clearNotifications(me);
		seedNotifications(me, org, 4);

		await page.goto('/notifications');
		await expect(page.locator('.nav-badge')).toHaveText('4', { timeout: 15_000 });

		await page.getByRole('button', { name: 'Mark all read' }).click();
		// Badge disappears (unread === 0 renders no badge).
		await expect(page.locator('.nav-badge')).toHaveCount(0, { timeout: 15_000 });

		clearNotifications(me);
	});

	test('unread filter and empty state', async ({ page }) => {
		await signInAndWait(page);
		const me = await currentUserId(page);
		clearNotifications(me);

		await page.goto('/notifications');
		// No notifications → empty state.
		await expect(page.getByText('No notifications yet.')).toBeVisible();

		// Unread filter on an empty inbox shows its own empty message.
		await page.getByRole('button', { name: /^Unread/ }).click();
		await expect(page.getByText('No unread notifications.')).toBeVisible();
	});
});
