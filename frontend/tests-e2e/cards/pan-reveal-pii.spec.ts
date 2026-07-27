import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';

import {
	API_BASE,
	authedTenantHeaders,
	currentTenantSlug,
	expect,
	signInAndWait,
	tenantPsql,
	test
} from '../fixtures/helpers';

/** psql against the CONTROL-plane DB where `organizations` lives. */
function controlPsql(query: string): string {
	return execFileSync(
		'psql',
		['-h', 'localhost', '-U', 'postgres', '-p', '5432', '-d', 'feohledger', '-tAc', query],
		{ env: { ...process.env, PGPASSWORD: 'postgres' }, stdio: ['ignore', 'pipe', 'pipe'] }
	).toString();
}

/**
 * PAN-reveal (`GET /api/cards/{id}/details`) — the PII path.
 *
 * Invariants exercised:
 *  - role-gated: only admin / ap_manager may reveal full card details;
 *    ap_clerk is 403'd.
 *  - every reveal writes a `card.details_viewed` audit row carrying ONLY
 *    `last_four` — never the full PAN / CVV (PII stays out of the trail).
 *  - the response returns the full number FROM the adapter (mock here) and
 *    is never persisted in our DB.
 *
 * The tenant is forced onto the `mock` card adapter so the reveal resolves
 * the canonical 4242… test PAN deterministically with no network call.
 */

const TOKEN = `e2e-reveal-${randomUUID()}`;

function slug(): string {
	return currentTenantSlug();
}

function useMockCardAdapter(): void {
	controlPsql(
		`UPDATE organizations
		    SET settings = jsonb_set(settings, '{cards}',
		        '{"enabled": true, "program_type": "byok", "provider": "mock", "region": "US"}'::jsonb)
		  WHERE slug = '${slug()}'`
	);
}

function restoreCardConfig(): void {
	controlPsql(
		`UPDATE organizations
		    SET settings = jsonb_set(settings, '{cards}',
		        '{"enabled": true, "program_type": "platform", "region": "US"}'::jsonb)
		  WHERE slug = '${slug()}'`
	);
}

function seedCard(): string {
	tenantPsql(
		`INSERT INTO virtual_cards (id, invoice_id, organization_id, card_provider, provider_card_id, amount_limit, currency, status, last_four, created_at, updated_at)
		 SELECT gen_random_uuid(), i.id, i.organization_id, 'mock', '${TOKEN}', 750.00, 'USD', 'active', '4242', now(), now()
		 FROM invoices i LIMIT 1`
	);
	return tenantPsql(`SELECT id FROM virtual_cards WHERE provider_card_id = '${TOKEN}'`).trim();
}

function purge(cardId: string): void {
	// audit_log is append-only (SOX immutability trigger) — leave the
	// details-viewed row; it has no FK to virtual_cards.
	tenantPsql(`DELETE FROM virtual_cards WHERE id = '${cardId}'`);
}

test.describe('card PAN reveal (PII path)', () => {
	test.beforeAll(() => useMockCardAdapter());
	test.afterAll(() => restoreCardConfig());

	test('admin reveal returns the PAN and audits only last_four', async ({ page }) => {
		const cardId = seedCard();
		try {
			const headers = await authedTenantHeaders(page);
			const resp = await page.request.get(`${API_BASE}/api/cards/${cardId}/details`, { headers });
			expect(resp.status()).toBe(200);
			const body = (await resp.json()) as { card_number: string; cvv: string };
			// The reveal endpoint returns the full PAN (to forward to the vendor).
			expect(body.card_number).toBe('4242424242424242');
			expect(body.cvv).toBe('123');

			// The audit row records the access but ONLY the last_four — the
			// PAN / CVV must never enter the audit trail.
			const audit = tenantPsql(
				`SELECT details FROM audit_log WHERE entity_type = 'virtual_card' AND action = 'card.details_viewed' AND entity_id = '${cardId}'`
			).trim();
			expect(audit.length).toBeGreaterThan(0);
			const details = JSON.parse(audit);
			expect(details.last_four).toBe('4242');
			expect(audit).not.toMatch(/4242424242424242/);
			expect(audit).not.toMatch(/"123"/);
		} finally {
			purge(cardId);
		}
	});

	test('ap_clerk is forbidden from revealing card details', async ({ page, tenantClerk }) => {
		const cardId = seedCard();
		try {
			await signInAndWait(page, tenantClerk);
			const headers = await authedTenantHeaders(page);
			const resp = await page.request.get(`${API_BASE}/api/cards/${cardId}/details`, { headers });
			expect(resp.status()).toBe(403);
			// A forbidden reveal must NOT write a details-viewed audit row.
			const audit = tenantPsql(
				`SELECT count(*) FROM audit_log WHERE entity_type = 'virtual_card' AND action = 'card.details_viewed' AND entity_id = '${cardId}'`
			).trim();
			expect(audit).toBe('0');
		} finally {
			purge(cardId);
		}
	});
});
