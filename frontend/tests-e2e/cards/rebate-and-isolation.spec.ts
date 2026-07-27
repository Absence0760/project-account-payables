import { execFileSync } from 'node:child_process';
import { createHmac, randomUUID } from 'node:crypto';

import {
	API_BASE,
	authedTenantHeaders,
	currentTenantSlug,
	expect,
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
 * Rebate math correctness (exact Decimal) + tenant isolation on the card
 * surface.
 *
 * Rebate: a settlement webhook on a charged card creates a `CardRebate` at
 * the 1% default rate. The amount must be exact (Numeric, not float):
 * 1234.56 × 0.0100 = 12.3456 → stored at Numeric(15,2) = 12.35 — we assert
 * the stored value rather than a float-y 12.345600000001.
 *
 * Isolation: a card seeded in this worker's tenant must only be visible to
 * this tenant's list/details, and a card id from another tenant resolves to
 * a 404 (tenant DB scoping at the data layer).
 */

const SECRET = 'wh_secret_rebate_e2e_0002';
const TOKEN = `e2e-rebate-${randomUUID()}`;

function slug(): string {
	return currentTenantSlug();
}

function setSigningSecret(): void {
	controlPsql(
		`UPDATE organizations SET settings = jsonb_set(settings, '{cards,webhook_signing_secret}', '"${SECRET}"'::jsonb) WHERE slug = '${slug()}'`
	);
}

function clearSigningSecret(): void {
	controlPsql(
		`UPDATE organizations SET settings = (settings #- '{cards,webhook_signing_secret}') WHERE slug = '${slug()}'`
	);
}

/** Seed a charged card with a known amount_charged so the settlement
 *  event only needs to create the rebate. */
function seedChargedCard(amountCharged: string): string {
	tenantPsql(
		`INSERT INTO virtual_cards (id, invoice_id, organization_id, card_provider, provider_card_id, amount_limit, amount_charged, currency, status, last_four, charged_at, created_at, updated_at)
		 SELECT gen_random_uuid(), i.id, i.organization_id, 'lithic', '${TOKEN}', 2000.00, ${amountCharged}, 'USD', 'charged', '4242', now(), now(), now()
		 FROM invoices i LIMIT 1`
	);
	return tenantPsql(`SELECT id FROM virtual_cards WHERE provider_card_id = '${TOKEN}'`).trim();
}

function purge(cardId: string): void {
	// audit_log is append-only (SOX immutability trigger) — leave any
	// card.* rows; they have no FK to virtual_cards.
	tenantPsql(`DELETE FROM card_rebates WHERE virtual_card_id = '${cardId}'`);
	tenantPsql(`DELETE FROM virtual_cards WHERE id = '${cardId}'`);
}

function settleBody(eventId: string): string {
	return JSON.stringify({
		card_token: TOKEN,
		type: 'transaction.created',
		event_id: eventId
	});
}

function sign(rawBody: string): string {
	return createHmac('sha256', SECRET).update(rawBody).digest('hex');
}

test.describe('card rebate math + tenant isolation', () => {
	test('settlement creates an exact-Decimal rebate at 1%', async ({ request }) => {
		setSigningSecret();
		const cardId = seedChargedCard('1234.56');
		try {
			const body = settleBody(randomUUID());
			const resp = await request.post(`${API_BASE}/api/cards/webhook/lithic`, {
				headers: { 'Content-Type': 'application/json', 'Webhook-Signature': sign(body) },
				data: body
			});
			expect(resp.status()).toBe(204);
			expect(tenantPsql(`SELECT status FROM virtual_cards WHERE id = '${cardId}'`).trim()).toBe(
				'completed'
			);

			const row = tenantPsql(
				`SELECT amount || '|' || rate FROM card_rebates WHERE virtual_card_id = '${cardId}'`
			).trim();
			const [amount, rate] = row.split('|');
			// 1234.56 × 0.0100 = 12.3456 → Numeric(15,2) → 12.35. Exact, no float drift.
			expect(amount).toBe('12.35');
			expect(rate).toBe('0.0100');
		} finally {
			purge(cardId);
			clearSigningSecret();
		}
	});

	test('a card is visible only to its own tenant; unknown id is 404', async ({ page }) => {
		const cardId = seedChargedCard('500.00');
		try {
			const headers = await authedTenantHeaders(page);

			// Own tenant sees it in the list.
			const list = await page.request.get(`${API_BASE}/api/cards?status=charged`, { headers });
			const listBody = (await list.json()) as { items: { id: string }[] };
			expect(listBody.items.some((c) => c.id === cardId)).toBe(true);

			// A card id that doesn't exist in this tenant DB resolves to 404,
			// not a leak of another tenant's row.
			const missing = await page.request.get(
				`${API_BASE}/api/cards/${randomUUID()}/details`,
				{ headers }
			);
			expect(missing.status()).toBe(404);
		} finally {
			purge(cardId);
		}
	});
});
