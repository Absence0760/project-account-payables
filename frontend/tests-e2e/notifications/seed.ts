/**
 * Shared notification seeding for the `tests-e2e/notifications/` specs.
 *
 * Notifications are only created as a side effect of a real workflow event
 * (an assignment, an approval, a payment), so a spec that needs a KNOWN inbox
 * — n unread, m already read — has to write the rows itself. These helpers go
 * straight at the worker tenant's `notifications` table via `tenantPsql`, the
 * same route the other specs use for state the API doesn't expose.
 *
 * Not a `*.spec.ts`, so Playwright's default `testMatch` never collects it.
 */
import { API_BASE, authedTenantHeaders, tenantPsql } from '../fixtures/helpers';

type Page = import('@playwright/test').Page;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The signed-in user's id — notifications are addressed per recipient. */
export async function currentUserId(page: Page): Promise<string> {
	const headers = await authedTenantHeaders(page);
	const resp = await page.request.get(`${API_BASE}/api/auth/me`, { headers });
	const body = (await resp.json()) as { id: string };
	return body.id;
}

/** The current worker tenant's organization_id — pulled off any seeded row.
 *  Invoices always exist in the seed, and carry organization_id. */
export function orgId(): string {
	const out = tenantPsql('SELECT organization_id FROM invoices LIMIT 1');
	const id = out
		.split('\n')
		.map((l) => l.trim())
		.find((l) => /^[0-9a-f-]{36}$/i.test(l));
	if (!id) throw new Error('no seeded invoice to borrow organization_id from');
	return id;
}

/** Insert `n` notifications addressed to `recipient` directly into the
 *  worker's tenant DB and return their ids. Self-seeding so the spec doesn't
 *  depend on app state that may not have fired a real event yet.
 *
 *  `read: true` stamps `read_at`, which is what makes an inbox whose whole-set
 *  count and unread count are different numbers — the thing the chip-count
 *  spec needs and no real-event path can produce on demand. */
export function seedNotifications(
	recipient: string,
	org: string,
	n: number,
	opts: { entityId?: string; title?: string; read?: boolean } = {}
): string[] {
	const entity = opts.entityId ? `'${opts.entityId}'::uuid` : '(SELECT id FROM invoices LIMIT 1)';
	const title = opts.title ?? 'E2E notification';
	const readAt = opts.read ? 'now()' : 'NULL';
	const out = tenantPsql(
		`INSERT INTO notifications
		   (id, correlation_id, organization_id, recipient_user_id, event_type,
		    entity_type, entity_id, title, body, read_at)
		 SELECT gen_random_uuid(), gen_random_uuid(), '${org}'::uuid, '${recipient}'::uuid,
		        'invoice_approved', 'invoice', ${entity}, '${title}', 'seeded body', ${readAt}
		 FROM generate_series(1, ${n})
		 RETURNING id`
	);
	return out
		.split('\n')
		.map((l) => l.trim())
		.filter((l) => UUID.test(l));
}

export function clearNotifications(recipient: string) {
	tenantPsql(`DELETE FROM notifications WHERE recipient_user_id = '${recipient}'::uuid`);
}
