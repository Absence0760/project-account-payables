import { execFileSync } from 'node:child_process';

import {
	API_BASE,
	authedTenantHeaders,
	currentTenantSlug,
	expect,
	tenantPsql,
	test
} from '../fixtures/helpers';

/** Run psql against the CONTROL-plane DB (`feohledger`) where
 *  `organizations` lives — `tenantPsql` only reaches the per-tenant
 *  `feoh_<slug>` DB, which has no organizations table. */
function controlPsql(query: string): string {
	return execFileSync(
		'psql',
		['-h', 'localhost', '-U', 'postgres', '-p', '5432', '-d', 'feohledger', '-tAc', query],
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

/** A seeded invoice id, for the one case that is refused before the endpoint
 *  ever looks at the invoice (the cards-disabled 400) — so it needs no vendor.
 *  It still orders by the primary key: an unordered `LIMIT 1` is the defect
 *  `aPayableInvoiceId` below carried, and the idiom should not survive
 *  anywhere in this file. Empty is a missing fixture, not a passing test. */
function anInvoiceId(): string {
	const id = tenantPsql(`SELECT id FROM invoices ORDER BY id LIMIT 1`).trim();
	expect(id, 'this tenant has no invoices at all — re-seed the e2e tenants').not.toBe('');
	return id;
}

/**
 * A seeded invoice this tenant can actually mint a card against.
 *
 * Every clause is load-bearing, because each one is a way `generate_cards`
 * declines an invoice **without failing the request** — it `continue`s past it
 * and returns a cheerful `201` with an empty `items`:
 *
 *  - **payable status** — mirrors `PAYABLE_INVOICE_STATUSES`
 *    (`backend/app/api/payments.py`), which `generate_cards` filters on. The
 *    lean e2e seed's first invoice is deliberately `new`, so `anInvoiceId()`
 *    alone was never safe here.
 *  - **a vendor** (the inner join) and **not `payments_blocked`** — minting a
 *    card moves money, so the endpoint runs the same compliance gate the
 *    payment-run leg does, and skips an invoice with no screenable vendor or a
 *    blocked one.
 *  - **`ORDER BY i.id`** — the real defect. `LIMIT 1` over an *unordered* set
 *    lets Postgres return whichever row it likes, so which invoice this spec
 *    tested depended on heap order, which shifts as other specs write to the
 *    shared tenant. On a freshly-seeded tenant every payable invoice carries a
 *    vendor and it passed; on a tenant holding a vendorless payable invoice
 *    stranded by an earlier spec it drew that row and failed — reproducibly,
 *    but looking exactly like a flake. `id` is the primary key, so this is a
 *    total order and stable across runs. **Never reintroduce a bare
 *    `LIMIT 1`.**
 *
 * Fails loudly when nothing qualifies rather than returning `''`: an empty id
 * would POST a batch matching no invoice, collect the same empty-`items` 201,
 * and turn a missing fixture into a wrong-looking assertion further down.
 */
function aPayableInvoiceId(): string {
	const id = tenantPsql(
		`SELECT i.id
		   FROM invoices i
		   JOIN vendors v ON v.id = i.vendor_id
		  WHERE i.status IN ('approved', 'posted_in_erp', 'payment_scheduled')
		    AND NOT v.payments_blocked
		  ORDER BY i.id
		  LIMIT 1`
	).trim();
	expect(
		id,
		'no payable invoice with an unblocked vendor in this tenant, so ' +
			'POST /api/cards/generate has nothing it would mint a card for — re-seed ' +
			'the e2e tenants (python backend/scripts/seed.py)'
	).not.toBe('');
	return id;
}

test.describe('virtual card lifecycle', () => {
	test.beforeAll(() => useMockCardAdapter());
	test.afterAll(() => restoreCardConfig());

	test('issue → list → cancel writes audit rows and refuses double-cancel', async ({ page }) => {
		const invoiceId = aPayableInvoiceId();
		purgeGenerated(invoiceId);
		const headers = await authedTenantHeaders(page);

		// Issue a card for the invoice.
		const gen = await page.request.post(`${API_BASE}/api/cards/generate`, {
			headers,
			data: { invoice_ids: [invoiceId] }
		});
		expect(gen.status()).toBe(201);
		const genBody = (await gen.json()) as { items: { id: string; status: string }[] };
		// A skip inside `generate_cards` is silent — 201 with an empty `items` —
		// so name the remaining causes here. `aPayableInvoiceId` has already
		// screened out the status / missing-vendor / blocked-vendor ones, which
		// leaves a live sanctions or KYC verdict, or a card adapter that refused.
		expect(
			genBody.items.length,
			'the endpoint accepted the batch but minted nothing: the invoice was skipped ' +
				'by the compliance gate or the card adapter refused'
		).toBe(1);
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
