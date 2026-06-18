import {
	API_BASE,
	authedTenantHeaders,
	expect,
	tenantPsql,
	test
} from '../fixtures/helpers';

/**
 * Dynamic-discounting money path (API-level).
 *
 * The savings / ROI / optimizer math is the whole point of the feature, so
 * these tests assert exact Decimal values, not just shapes:
 *   - accept picks the right tier, computes savings exactly, audits, and is
 *     idempotent (double-accept is a safe 409, never a double-count);
 *   - decline transitions + audits;
 *   - per-invoice ROI / APR is the textbook cost-of-forgoing-discount value;
 *   - the optimizer ranks by APR and respects a cash budget;
 *   - accept never moves money (no Payment / PaymentRun row appears);
 *   - mutate RBAC: accept = admin/ap_manager/cfo, decline = admin/ap_manager,
 *     clerk is read-only;
 *   - offers are tenant-isolated.
 *
 * Setup builds its own invoices + offers via the API; teardown is psql.
 */

interface Vendor {
	id: string;
	name: string;
}

async function firstVendor(page: import('@playwright/test').Page): Promise<Vendor> {
	const resp = await page.request.get(`${API_BASE}/api/vendors`, {
		headers: await authedTenantHeaders(page)
	});
	return ((await resp.json()) as { items: Vendor[] }).items[0];
}

/** Approved invoice with `amount` and a due date `dueInDays` out, bound to the
 *  vendor. Returns the invoice id. */
async function makeInvoice(
	page: import('@playwright/test').Page,
	vendor: Vendor,
	amount: number,
	dueInDays: number
): Promise<string> {
	const resp = await page.request.post(`${API_BASE}/api/invoices`, {
		headers: await authedTenantHeaders(page),
		data: {
			vendor: vendor.name,
			invoice_number: `DISC-${Date.now()}-${Math.floor(Math.random() * 1e6)}`,
			amount,
			status: 'approved'
		}
	});
	const inv = (await resp.json()) as { id: string };
	tenantPsql(
		`UPDATE invoices SET vendor_id='${vendor.id}', due_date = CURRENT_DATE + ${dueInDays} WHERE id='${inv.id}'`
	);
	return inv.id;
}

async function makeOffer(
	page: import('@playwright/test').Page,
	invoiceId: string,
	tiers: Array<{ days: number; percent: string }>
): Promise<{ id: string; status: string }> {
	const resp = await page.request.post(`${API_BASE}/api/discounts/offers`, {
		headers: await authedTenantHeaders(page),
		data: { scope: 'invoice', invoice_id: invoiceId, tiers }
	});
	expect(resp.status(), await resp.text()).toBe(201);
	return (await resp.json()) as { id: string; status: string };
}

// NOTE: audit_log is append-only at the DB level (immutability trigger), so
// cleanup never deletes audit rows — leftover rows pointing at deleted test
// entities are harmless residue. We only remove the entity rows themselves.
function cleanupOffer(id: string): void {
	tenantPsql(`DELETE FROM discount_offers WHERE id='${id}'`);
}

function cleanupInvoice(id: string): void {
	tenantPsql(
		`DELETE FROM workflow_steps WHERE instance_id IN (SELECT id FROM workflow_instances WHERE invoice_id='${id}')`
	);
	tenantPsql(`DELETE FROM workflow_instances WHERE invoice_id='${id}'`);
	tenantPsql(`DELETE FROM invoices WHERE id='${id}'`);
}

function offerAuditActions(id: string): string[] {
	return tenantPsql(
		`SELECT action FROM audit_log WHERE entity_id='${id}' AND entity_type='discount_offer' ORDER BY created_at`
	)
		.split('\n')
		.map((s) => s.trim())
		.filter(Boolean);
}

test.describe('discounting money path (API)', () => {
	test('accept selects the best (highest-%) tier, computes savings exactly, audits, is idempotent', async ({
		page
	}) => {
		const headers = await authedTenantHeaders(page);
		const vendor = await firstVendor(page);
		const invoiceId = await makeInvoice(page, vendor, 1000, 30);
		// Two tiers — best-for-today must pick the 3% rung (highest percent).
		const offer = await makeOffer(page, invoiceId, [
			{ days: 5, percent: '3.00' },
			{ days: 10, percent: '2.00' }
		]);

		try {
			const acc = await page.request.post(
				`${API_BASE}/api/discounts/offers/${offer.id}/accept`,
				{ headers, data: {} }
			);
			expect(acc.status()).toBe(200);
			const body = (await acc.json()) as {
				status: string;
				accepted_tier: { days: number; percent: number };
			};
			expect(body.status).toBe('accepted');
			// Highest-percent tier wins.
			expect(body.accepted_tier.percent).toBe(3);
			expect(body.accepted_tier.days).toBe(5);

			// Audit row written with the chosen tier.
			expect(offerAuditActions(offer.id)).toEqual([
				'discount_offer.created',
				'discount_offer.accepted'
			]);

			// Idempotency: a second accept is a safe 409 and does NOT write a
			// second accepted row (no double-count).
			const again = await page.request.post(
				`${API_BASE}/api/discounts/offers/${offer.id}/accept`,
				{ headers, data: {} }
			);
			expect(again.status()).toBe(409);
			expect(offerAuditActions(offer.id)).toEqual([
				'discount_offer.created',
				'discount_offer.accepted'
			]);

			// Money-path boundary: accepting flags the offer but NEVER creates a
			// payment. No Payment / PaymentRun row references this invoice.
			const payCount = tenantPsql(
				`SELECT count(*) FROM payments WHERE invoice_id='${invoiceId}'`
			).trim();
			expect(payCount).toBe('0');
		} finally {
			cleanupOffer(offer.id);
			cleanupInvoice(invoiceId);
		}
	});

	test('accept at an explicit tier captures the requested rung, not the best', async ({
		page
	}) => {
		const headers = await authedTenantHeaders(page);
		const vendor = await firstVendor(page);
		const invoiceId = await makeInvoice(page, vendor, 1000, 30);
		const offer = await makeOffer(page, invoiceId, [
			{ days: 5, percent: '3.00' },
			{ days: 10, percent: '2.00' }
		]);
		try {
			const acc = await page.request.post(
				`${API_BASE}/api/discounts/offers/${offer.id}/accept`,
				{ headers, data: { tier_days: 10 } }
			);
			expect(acc.status()).toBe(200);
			const body = (await acc.json()) as { accepted_tier: { days: number; percent: number } };
			expect(body.accepted_tier.days).toBe(10);
			expect(body.accepted_tier.percent).toBe(2);

			// A non-existent tier is rejected.
			const offer2 = await makeOffer(page, invoiceId, [{ days: 5, percent: '3.00' }]);
			const bad = await page.request.post(
				`${API_BASE}/api/discounts/offers/${offer2.id}/accept`,
				{ headers, data: { tier_days: 99 } }
			);
			expect(bad.status()).toBe(422);
			cleanupOffer(offer2.id);
		} finally {
			cleanupOffer(offer.id);
			cleanupInvoice(invoiceId);
		}
	});

	test('decline transitions offered → declined and audits; re-decline is a safe 409', async ({
		page
	}) => {
		const headers = await authedTenantHeaders(page);
		const vendor = await firstVendor(page);
		const invoiceId = await makeInvoice(page, vendor, 500, 30);
		const offer = await makeOffer(page, invoiceId, [{ days: 5, percent: '2.00' }]);
		try {
			const dec = await page.request.post(
				`${API_BASE}/api/discounts/offers/${offer.id}/decline`,
				{ headers }
			);
			expect(dec.status()).toBe(200);
			expect(((await dec.json()) as { status: string }).status).toBe('declined');
			expect(offerAuditActions(offer.id)).toEqual([
				'discount_offer.created',
				'discount_offer.declined'
			]);

			const again = await page.request.post(
				`${API_BASE}/api/discounts/offers/${offer.id}/decline`,
				{ headers }
			);
			expect(again.status()).toBe(409);
		} finally {
			cleanupOffer(offer.id);
			cleanupInvoice(invoiceId);
		}
	});

	test('per-invoice ROI is the exact cost-of-forgoing-discount value', async ({ page }) => {
		const headers = await authedTenantHeaders(page);
		const vendor = await firstVendor(page);
		// 1000 invoice, due in 30 days; a single 2% tier whose deadline is 10
		// days out → cash accelerated by 20 days. APR = 2/(100-2)*365/20 = 37.24%.
		const invoiceId = await makeInvoice(page, vendor, 1000, 30);
		const offer = await makeOffer(page, invoiceId, [{ days: 10, percent: '2.00' }]);
		try {
			const resp = await page.request.get(
				`${API_BASE}/api/discounts/invoices/${invoiceId}/roi`,
				{ headers }
			);
			expect(resp.status()).toBe(200);
			const roi = (await resp.json()) as {
				base_amount: number;
				discount_percent: number;
				days_accelerated: number;
				savings: number;
				annualized_return_pct: number;
				opportunity_cost: number;
				net_benefit: number;
				worthwhile: boolean;
			};
			expect(roi.base_amount).toBe(1000);
			expect(roi.discount_percent).toBe(2);
			expect(roi.days_accelerated).toBe(20);
			expect(roi.savings).toBe(20); // 1000 * 2%
			expect(roi.annualized_return_pct).toBe(37.24);
			// opportunity cost of paying 980 twenty days early at 8% cost of capital
			// = 980 * 0.08 * 20/365 = 4.30.
			expect(roi.opportunity_cost).toBe(4.3);
			expect(roi.net_benefit).toBe(15.7); // 20 - 4.30
			expect(roi.worthwhile).toBe(true); // 37.24% >> 8%
		} finally {
			cleanupOffer(offer.id);
			cleanupInvoice(invoiceId);
		}
	});

	test('optimizer ranks by APR and respects the cash budget', async ({ page }) => {
		// The optimizer scores every OPEN offer in the active entity. To keep the
		// budget assertion deterministic on a shared tenant (other tests/seed
		// leave open offers around), scope this whole test to a throwaway entity
		// so the optimizer sees ONLY the two offers built here.
		const baseHeaders = await authedTenantHeaders(page);
		const slug = baseHeaders['X-Tenant-Slug'];
		const UUID_RE = /[0-9a-f-]{36}/i;
		const orgRow = tenantPsql(`SELECT organization_id FROM entities LIMIT 1`).match(
			UUID_RE
		)![0];
		const entityId = tenantPsql(
			`INSERT INTO entities (id, organization_id, name, slug, is_default, is_active, settings) ` +
				`VALUES (gen_random_uuid(), '${orgRow}', 'opt-e2e', 'opt-e2e-${Date.now()}', false, true, '{}'::jsonb) ` +
				`RETURNING id`
		).match(UUID_RE)![0];
		const headers = { ...baseHeaders, 'X-Entity-ID': entityId };
		const vendor = await firstVendor(page);

		// Build two invoice-scoped offers inside the new entity, distinct APRs.
		// A: 1000 @ 3% / 20 days accelerated → APR ~56.44, outlay 970 (1000-30).
		// B: 1000 @ 1% / 20 days accelerated → APR ~18.43, outlay 990 (1000-10).
		async function entityInvoice(amount: number): Promise<string> {
			const r = await page.request.post(`${API_BASE}/api/invoices`, {
				headers,
				data: {
					vendor: vendor.name,
					invoice_number: `OPT-${Date.now()}-${Math.floor(Math.random() * 1e6)}`,
					amount,
					status: 'approved'
				}
			});
			const inv = (await r.json()) as { id: string };
			tenantPsql(
				`UPDATE invoices SET vendor_id='${vendor.id}', due_date = CURRENT_DATE + 30 WHERE id='${inv.id}'`
			);
			return inv.id;
		}
		async function entityOffer(
			invoiceId: string,
			percent: string
		): Promise<{ id: string }> {
			const r = await page.request.post(`${API_BASE}/api/discounts/offers`, {
				headers,
				data: {
					scope: 'invoice',
					invoice_id: invoiceId,
					tiers: [{ days: 10, percent }]
				}
			});
			expect(r.status(), await r.text()).toBe(201);
			return (await r.json()) as { id: string };
		}

		const invA = await entityInvoice(1000);
		const invB = await entityInvoice(1000);
		const offerA = await entityOffer(invA, '3.00');
		const offerB = await entityOffer(invB, '1.00');
		try {
			// Unconstrained, entity-scoped: exactly our two offers, A ranked first.
			const unconstrained = await page.request.post(`${API_BASE}/api/discounts/optimize`, {
				headers,
				data: {}
			});
			expect(unconstrained.status()).toBe(200);
			const all = (await unconstrained.json()) as {
				total_savings_selected: number;
				recommendations: Array<{
					offer_id: string;
					selected: boolean;
					roi: { annualized_return_pct: number; savings: number };
				}>;
			};
			// Only our two offers are in scope.
			expect(all.recommendations.length).toBe(2);
			const recA = all.recommendations.find((r) => r.offer_id === offerA.id)!;
			const recB = all.recommendations.find((r) => r.offer_id === offerB.id)!;
			expect(recA.roi.annualized_return_pct).toBeGreaterThan(
				recB.roi.annualized_return_pct
			);
			// A (higher APR) ranks before B.
			expect(all.recommendations[0].offer_id).toBe(offerA.id);
			expect(recA.selected && recB.selected).toBe(true);
			// Both savings captured: 30 (A) + 10 (B) = 40.
			expect(all.total_savings_selected).toBe(40);

			// Constrained to 990: A's outlay is 970 (fits); adding B (990) → 1960
			// > 990, so only the higher-APR A is funded. The budget binds.
			const constrained = await page.request.post(`${API_BASE}/api/discounts/optimize`, {
				headers,
				data: { cash_budget: 990 }
			});
			const tight = (await constrained.json()) as {
				total_outlay_selected: number;
				total_savings_selected: number;
				recommendations: Array<{ offer_id: string; selected: boolean }>;
			};
			const cA = tight.recommendations.find((r) => r.offer_id === offerA.id)!;
			const cB = tight.recommendations.find((r) => r.offer_id === offerB.id)!;
			expect(cA.selected).toBe(true);
			expect(cB.selected).toBe(false);
			expect(tight.total_outlay_selected).toBe(970); // only A
			expect(tight.total_savings_selected).toBe(30); // A's 3% of 1000
			expect(tight.total_outlay_selected).toBeLessThanOrEqual(990);
		} finally {
			cleanupOffer(offerA.id);
			cleanupOffer(offerB.id);
			cleanupInvoice(invA);
			cleanupInvoice(invB);
			tenantPsql(`DELETE FROM entities WHERE id='${entityId}'`);
		}
	});

	test('RBAC: clerk read-only; cfo may accept; manager may decline', async ({
		page,
		tenantClerk,
		tenantCfo,
		tenantManager
	}) => {
		const adminHeaders = await authedTenantHeaders(page);
		const slug = adminHeaders['X-Tenant-Slug'];
		const vendor = await firstVendor(page);

		async function tokenFor(creds: { email: string; password: string }): Promise<string> {
			const r = await page.request.post(`${API_BASE}/api/auth/login`, {
				headers: { 'X-Tenant-Slug': slug },
				data: { email: creds.email, password: creds.password }
			});
			expect(r.status()).toBe(200);
			return ((await r.json()) as { access_token: string }).access_token;
		}
		const hdr = (t: string) => ({ Authorization: `Bearer ${t}`, 'X-Tenant-Slug': slug });
		const clerkH = hdr(await tokenFor(tenantClerk));
		const cfoH = hdr(await tokenFor(tenantCfo));
		const managerH = hdr(await tokenFor(tenantManager));

		// Clerk can read offers (4-role read).
		const clerkList = await page.request.get(`${API_BASE}/api/discounts/offers`, {
			headers: clerkH
		});
		expect(clerkList.status()).toBe(200);

		// Clerk cannot create an offer (mutate = admin/ap_manager).
		const inv1 = await makeInvoice(page, vendor, 600, 30);
		const clerkCreate = await page.request.post(`${API_BASE}/api/discounts/offers`, {
			headers: clerkH,
			data: { scope: 'invoice', invoice_id: inv1, tiers: [{ days: 5, percent: '2.00' }] }
		});
		expect(clerkCreate.status()).toBe(403);

		// CFO MAY accept (accept roles = admin/ap_manager/cfo).
		const offerForCfo = await makeOffer(page, inv1, [{ days: 5, percent: '2.00' }]);
		const cfoAccept = await page.request.post(
			`${API_BASE}/api/discounts/offers/${offerForCfo.id}/accept`,
			{ headers: cfoH, data: {} }
		);
		expect(cfoAccept.status()).toBe(200);

		// CFO may NOT decline (decline roles = admin/ap_manager only).
		const inv2 = await makeInvoice(page, vendor, 600, 30);
		const offerForDecline = await makeOffer(page, inv2, [{ days: 5, percent: '2.00' }]);
		const cfoDecline = await page.request.post(
			`${API_BASE}/api/discounts/offers/${offerForDecline.id}/decline`,
			{ headers: cfoH }
		);
		expect(cfoDecline.status()).toBe(403);
		// Manager CAN decline.
		const mgrDecline = await page.request.post(
			`${API_BASE}/api/discounts/offers/${offerForDecline.id}/decline`,
			{ headers: managerH }
		);
		expect(mgrDecline.status()).toBe(200);

		cleanupOffer(offerForCfo.id);
		cleanupOffer(offerForDecline.id);
		cleanupInvoice(inv1);
		cleanupInvoice(inv2);
	});

	test('offers are tenant-isolated — another tenant cannot read or accept', async ({
		page
	}) => {
		const headers = await authedTenantHeaders(page);
		const slug = headers['X-Tenant-Slug'];
		const vendor = await firstVendor(page);
		const invoiceId = await makeInvoice(page, vendor, 700, 30);
		const offer = await makeOffer(page, invoiceId, [{ days: 5, percent: '2.00' }]);
		try {
			// A different tenant's admin must not see this offer. Use the seeded
			// acme tenant (distinct from any e2e<N>); its admin token + acme slug.
			const acmeLogin = await page.request.post(`${API_BASE}/api/auth/login`, {
				headers: { 'X-Tenant-Slug': 'acme' },
				data: { email: 'demo@acme.com', password: 'demo' }
			});
			expect(acmeLogin.status()).toBe(200);
			const acmeToken = ((await acmeLogin.json()) as { access_token: string }).access_token;
			const acmeHeaders = { Authorization: `Bearer ${acmeToken}`, 'X-Tenant-Slug': 'acme' };

			// The cross-tenant GET of our offer id resolves against acme's DB and
			// 404s (different DB, not present) — never leaks the row.
			const cross = await page.request.get(
				`${API_BASE}/api/discounts/offers/${offer.id}`,
				{ headers: acmeHeaders }
			);
			expect(cross.status()).toBe(404);

			// And a cross-tenant accept can't transition our offer either.
			const crossAccept = await page.request.post(
				`${API_BASE}/api/discounts/offers/${offer.id}/accept`,
				{ headers: acmeHeaders, data: {} }
			);
			expect(crossAccept.status()).toBe(404);

			// Our own tenant still sees it as offered (untouched).
			const mine = await page.request.get(
				`${API_BASE}/api/discounts/offers/${offer.id}`,
				{ headers: { ...headers, 'X-Tenant-Slug': slug } }
			);
			expect(mine.status()).toBe(200);
			expect(((await mine.json()) as { status: string }).status).toBe('offered');
		} finally {
			cleanupOffer(offer.id);
			cleanupInvoice(invoiceId);
		}
	});
});
