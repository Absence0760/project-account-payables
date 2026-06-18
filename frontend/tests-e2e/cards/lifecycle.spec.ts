import { execFileSync } from 'node:child_process';

import {
	API_BASE,
	authedTenantHeaders,
	currentTenantSlug,
	expect,
	tenantPsql,
	test
} from '../fixtures/helpers';

/** Run psql against the CONTROL-plane DB (`account_payables`) where
 *  `organizations` lives — `tenantPsql` only reaches the per-tenant
 *  `ap_<slug>` DB, which has no organizations table. */
function controlPsql(query: string): string {
	return execFileSync(
		'psql',
		['-h', 'localhost', '-U', 'postgres', '-p', '5432', '-d', 'account_payables', '-tAc', query],
		{ env: { ...process.env, PGPASSWORD: 'postgres' }, stdio: ['ignore', 'pipe', 'pipe'] }
	).toString();
}

/**
 * Virtual-card issuance lifecycle + the audit/RBAC guarantees on the
 * money path. Exercised through the authenticated API (there is no
 * employee-facing `/cards` route — the card UI is the `/payments` Cards
 * tab; the issuance / cancel / details endpoints under `/api/cards` are
 * the real money + PII surface).
 *
 * Setup forces the e2e tenant onto the `mock` card adapter (BYOK shape)
 * so `/generate` and `/cancel` resolve deterministically with no network
 * call — the seeded `platform`/`US` config would otherwise reach for the
 * live Lithic sandbox.
 */

const SLUG = () => currentTenantSlug();

/** Point the tenant's card config at the in-process mock adapter. The
 *  generate/details/cancel endpoints read `Organization.settings.cards`;
 *  BYOK + provider=mock keeps `cards.enabled` true while avoiding the
 *  platform→lithic network path. Restored in afterAll. */
function useMockCardAdapter(): void {
	controlPsql(
		`UPDATE organizations
		    SET settings = jsonb_set(
		        settings,
		        '{cards}',
		        '{"enabled": true, "program_type": "byok", "provider": "mock", "region": "US"}'::jsonb
		    )
		  WHERE slug = '${SLUG()}'`
	);
}

function restoreCardConfig(): void {
	controlPsql(
		`UPDATE organizations
		    SET settings = jsonb_set(
		        settings,
		        '{cards}',
		        '{"enabled": true, "program_type": "platform", "region": "US"}'::jsonb
		    )
		  WHERE slug = '${SLUG()}'`
	);
}

function purgeGenerated(invoiceId: string): void {
	// audit_log is append-only (SOX immutability trigger); leave the
	// `card.*` rows the endpoints wrote — they don't FK virtual_cards.
	tenantPsql(
		`DELETE FROM card_rebates WHERE virtual_card_id IN (SELECT id FROM virtual_cards WHERE invoice_id = '${invoiceId}')`
	);
	tenantPsql(`DELETE FROM virtual_cards WHERE invoice_id = '${invoiceId}'`);
}

/** A seeded invoice id to issue cards against. */
function anInvoiceId(): string {
	return tenantPsql(`SELECT id FROM invoices LIMIT 1`).trim();
}

test.describe('virtual card lifecycle', () => {
	test.beforeAll(() => useMockCardAdapter());
	test.afterAll(() => restoreCardConfig());

	test('issue → list → cancel writes audit rows and refuses double-cancel', async ({ page }) => {
		const invoiceId = anInvoiceId();
		purgeGenerated(invoiceId);
		const headers = await authedTenantHeaders(page);

		// Issue a card for the invoice.
		const gen = await page.request.post(`${API_BASE}/api/cards/generate`, {
			headers,
			data: { invoice_ids: [invoiceId] }
		});
		expect(gen.status()).toBe(201);
		const genBody = (await gen.json()) as { items: { id: string; status: string }[] };
		expect(genBody.items.length).toBe(1);
		const card = genBody.items[0];
		expect(card.status).toBe('created');

		// It shows up in the list, scoped to this tenant.
		const list = await page.request.get(`${API_BASE}/api/cards`, { headers });
		const listBody = (await list.json()) as { items: { id: string }[] };
		expect(listBody.items.some((c) => c.id === card.id)).toBe(true);

		// Cancel it.
		const cancel = await page.request.post(`${API_BASE}/api/cards/${card.id}/cancel`, { headers });
		expect(cancel.status()).toBe(200);

		// Status is now cancelled in the DB.
		const status = tenantPsql(
			`SELECT status FROM virtual_cards WHERE id = '${card.id}'`
		).trim();
		expect(status).toBe('cancelled');

		// The cancel left an append-only audit row (invariant: status
		// transitions write audit). PII-free — only last_four + from/to.
		const audit = tenantPsql(
			`SELECT details FROM audit_log WHERE entity_type = 'virtual_card' AND action = 'card.cancelled' AND entity_id = '${card.id}'`
		).trim();
		expect(audit.length).toBeGreaterThan(0);
		const details = JSON.parse(audit);
		expect(details.to).toBe('cancelled');
		expect(details.from).toBe('created');
		expect(details.last_four).toBe('4242');
		// The audit row must NOT carry a full PAN.
		expect(audit).not.toMatch(/4242424242424242/);

		// Re-cancelling a cancelled card is a 409 (no silent re-transition).
		const recancel = await page.request.post(`${API_BASE}/api/cards/${card.id}/cancel`, { headers });
		expect(recancel.status()).toBe(409);

		purgeGenerated(invoiceId);
	});

	test('generate is refused (400) when cards are disabled', async ({ page }) => {
		const invoiceId = anInvoiceId();
		const headers = await authedTenantHeaders(page);
		// Flip the master switch off.
		controlPsql(
			`UPDATE organizations SET settings = jsonb_set(settings, '{cards,enabled}', 'false'::jsonb) WHERE slug = '${SLUG()}'`
		);
		try {
			const gen = await page.request.post(`${API_BASE}/api/cards/generate`, {
				headers,
				data: { invoice_ids: [invoiceId] }
			});
			expect(gen.status()).toBe(400);
		} finally {
			useMockCardAdapter(); // re-enable for the rest of the file
		}
	});
});
