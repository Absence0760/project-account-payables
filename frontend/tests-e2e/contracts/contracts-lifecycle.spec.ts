import type { Page } from '@playwright/test';

import { API_BASE, authedTenantHeaders, expect, tenantPsql, test } from '../fixtures/helpers';

/**
 * Contract Lifecycle Management — the critical money/control paths that the
 * shallow `contracts.spec.ts` doesn't reach:
 *
 *  - lifecycle transitions enforce valid source states (invalid → 409) and
 *    EACH writes exactly one append-only audit row,
 *  - RBAC: read = all four employee roles, mutate = admin/ap_manager only
 *    (clerk + cfo are 403 on every lifecycle mutation, create-po, upload),
 *  - contract→PO creation links a PO to the contract, copies money exactly,
 *    is gated to draft/active contracts, and is audited,
 *  - spend-to-contract attribution sums linked invoices in Decimal (rejected
 *    excluded) and the spend summary math (remaining / over_limit) is exact,
 *  - the contract-file download proxy refuses a cross-org key (404, no
 *    enumeration).
 *
 * Everything is API-driven (auth via cached JWT, same as the payment-run
 * sign-off spec) so the assertions exercise the backend guards directly, not
 * just the UI surface. Each test cleans up its rows via psql in `finally`.
 *
 * `test.use({ storageState: {} })` opts out of the default admin auto-login so
 * each test can choose its own role explicitly via `apiSignIn`.
 */
test.use({ storageState: { cookies: [], origins: [] } });

// Login is rate-limited; mint each role's JWT at most once and replay it.
const _tokenCache = new Map<string, string>();

async function _mintToken(
	page: Page,
	creds: { email: string; password: string }
): Promise<string> {
	const cached = _tokenCache.get(creds.email);
	if (cached) return cached;
	const resp = await page.request.post(`${API_BASE}/api/auth/login`, {
		data: { email: creds.email, password: creds.password }
	});
	expect(resp.status()).toBe(200);
	const token = ((await resp.json()) as { access_token: string }).access_token;
	expect(token).toBeTruthy();
	_tokenCache.set(creds.email, token);
	return token;
}

async function apiSignIn(page: Page, creds: { email: string; password: string }): Promise<void> {
	const token = await _mintToken(page, creds);
	await page.evaluate((t) => localStorage.setItem('auth_token', t), token);
}

interface Vendor {
	id: string;
	name: string;
}

async function getFirstVendor(page: Page): Promise<Vendor> {
	const resp = await page.request.get(`${API_BASE}/api/vendors`, {
		headers: await authedTenantHeaders(page)
	});
	const body = (await resp.json()) as { items: Vendor[] };
	expect(body.items.length).toBeGreaterThanOrEqual(1);
	return body.items[0];
}

type ContractRow = {
	id: string;
	status: string;
	contract_number: string;
	spend?: {
		invoiced_total: number;
		invoice_count: number;
		spend_limit: number | null;
		remaining: number | null;
		over_limit: boolean;
	} | null;
};

async function createContract(page: Page, data: Record<string, unknown>): Promise<ContractRow> {
	const resp = await page.request.post(`${API_BASE}/api/contracts`, {
		headers: await authedTenantHeaders(page),
		data
	});
	expect(resp.status(), await resp.text()).toBe(201);
	return (await resp.json()) as ContractRow;
}

async function getContract(page: Page, id: string): Promise<ContractRow> {
	const resp = await page.request.get(`${API_BASE}/api/contracts/${id}`, {
		headers: await authedTenantHeaders(page)
	});
	expect(resp.status()).toBe(200);
	return (await resp.json()) as ContractRow;
}

/** Count audit rows for an action against a contract id. */
function auditCount(action: string, entityId: string): number {
	const out = tenantPsql(
		`SELECT count(*) FROM audit_log WHERE action='${action}' AND entity_id='${entityId}'`
	);
	return parseInt(out.trim(), 10);
}

// NOTE: `audit_log` is append-only (DB immutability trigger) — cleanup never
// deletes audit rows. They carry no FK to contracts/invoices, so the orphaned
// rows are harmless and the contract/invoice DELETE below succeeds regardless.
function deleteContract(id: string): void {
	tenantPsql(`DELETE FROM contract_line_items WHERE contract_id='${id}'`);
	tenantPsql(`DELETE FROM contracts WHERE id='${id}'`);
}

/** Hard-delete an invoice + its workflow detritus (audit rows are immutable).
 *  Linking an invoice to an over-limit contract runs `refresh_warnings`, which
 *  can raise `exceptions` (e.g. `contract_noncompliant`) — clear those first or
 *  the FK blocks the invoice delete. */
function deleteInvoice(id: string): void {
	tenantPsql(`DELETE FROM exceptions WHERE invoice_id='${id}'`);
	tenantPsql(`DELETE FROM workflow_steps WHERE instance_id IN (SELECT id FROM workflow_instances WHERE invoice_id='${id}')`);
	tenantPsql(`DELETE FROM workflow_instances WHERE invoice_id='${id}'`);
	tenantPsql(`DELETE FROM invoice_line_items WHERE invoice_id='${id}'`);
	tenantPsql(`DELETE FROM invoices WHERE id='${id}'`);
}

test.describe('contract lifecycle transitions', () => {
	test.beforeEach(async ({ page, tenantAdmin }) => {
		await apiSignIn(page, tenantAdmin);
	});

	test('activate (draft→active) flips status and writes one audit row', async ({ page }) => {
		const vendor = await getFirstVendor(page);
		const number = `E2E-LC-ACT-${Date.now()}`;
		let id: string | null = null;
		try {
			const c = await createContract(page, {
				contract_number: number,
				vendor_id: vendor.id,
				contract_type: 'service',
				end_date: '2031-01-01'
			});
			id = c.id;
			expect(c.status).toBe('draft');
			expect(auditCount('contract.active', id)).toBe(0);

			const resp = await page.request.post(`${API_BASE}/api/contracts/${id}/activate`, {
				headers: await authedTenantHeaders(page)
			});
			expect(resp.status()).toBe(200);
			expect(((await resp.json()) as ContractRow).status).toBe('active');

			// Append-only invariant: exactly one new audit row for the transition.
			expect(auditCount('contract.active', id)).toBe(1);
		} finally {
			if (id) deleteContract(id);
		}
	});

	test('terminate from draft is rejected (409); from active it succeeds + audits', async ({
		page
	}) => {
		const vendor = await getFirstVendor(page);
		const number = `E2E-LC-TERM-${Date.now()}`;
		let id: string | null = null;
		try {
			const c = await createContract(page, {
				contract_number: number,
				vendor_id: vendor.id,
				contract_type: 'service',
				end_date: '2031-01-01'
			});
			id = c.id;

			// draft → terminate is NOT a valid source state.
			const bad = await page.request.post(`${API_BASE}/api/contracts/${id}/terminate`, {
				headers: await authedTenantHeaders(page)
			});
			expect(bad.status()).toBe(409);
			expect((await getContract(page, id)).status).toBe('draft');
			// A rejected transition must not write a phantom audit row.
			expect(auditCount('contract.terminated', id)).toBe(0);

			// Activate, then terminate — now valid.
			await page.request.post(`${API_BASE}/api/contracts/${id}/activate`, {
				headers: await authedTenantHeaders(page)
			});
			const ok = await page.request.post(`${API_BASE}/api/contracts/${id}/terminate`, {
				headers: await authedTenantHeaders(page)
			});
			expect(ok.status()).toBe(200);
			expect(((await ok.json()) as ContractRow).status).toBe('terminated');
			expect(auditCount('contract.terminated', id)).toBe(1);

			// Terminal state: a second terminate is rejected, no double-audit.
			const again = await page.request.post(`${API_BASE}/api/contracts/${id}/terminate`, {
				headers: await authedTenantHeaders(page)
			});
			expect(again.status()).toBe(409);
			expect(auditCount('contract.terminated', id)).toBe(1);
		} finally {
			if (id) deleteContract(id);
		}
	});

	test('cancel guards source state; renew rejects a terminated contract', async ({ page }) => {
		const vendor = await getFirstVendor(page);
		const number = `E2E-LC-CXL-${Date.now()}`;
		let id: string | null = null;
		try {
			const c = await createContract(page, {
				contract_number: number,
				vendor_id: vendor.id,
				contract_type: 'service',
				end_date: '2031-01-01'
			});
			id = c.id;

			// draft → cancel is valid.
			const cxl = await page.request.post(`${API_BASE}/api/contracts/${id}/cancel`, {
				headers: await authedTenantHeaders(page)
			});
			expect(cxl.status()).toBe(200);
			expect(((await cxl.json()) as ContractRow).status).toBe('cancelled');
			expect(auditCount('contract.cancelled', id)).toBe(1);

			// cancelled → renew is rejected (terminal-ish guard).
			const renew = await page.request.post(`${API_BASE}/api/contracts/${id}/renew`, {
				headers: await authedTenantHeaders(page),
				data: { end_date: '2032-01-01' }
			});
			expect(renew.status()).toBe(409);
			expect(auditCount('contract.renewed', id)).toBe(0);
		} finally {
			if (id) deleteContract(id);
		}
	});

	test('renew extends end_date, re-activates, and audits once', async ({ page }) => {
		const vendor = await getFirstVendor(page);
		const number = `E2E-LC-RENEW-${Date.now()}`;
		let id: string | null = null;
		try {
			const c = await createContract(page, {
				contract_number: number,
				vendor_id: vendor.id,
				contract_type: 'subscription',
				end_date: '2030-06-30'
			});
			id = c.id;
			await page.request.post(`${API_BASE}/api/contracts/${id}/activate`, {
				headers: await authedTenantHeaders(page)
			});

			// Renewal end_date must be strictly after the current one.
			const tooEarly = await page.request.post(`${API_BASE}/api/contracts/${id}/renew`, {
				headers: await authedTenantHeaders(page),
				data: { end_date: '2030-06-30' }
			});
			expect(tooEarly.status()).toBe(400);
			expect(auditCount('contract.renewed', id)).toBe(0);

			const ok = await page.request.post(`${API_BASE}/api/contracts/${id}/renew`, {
				headers: await authedTenantHeaders(page),
				data: { end_date: '2031-06-30', spend_limit: '99999.00' }
			});
			expect(ok.status()).toBe(200);
			const body = (await ok.json()) as ContractRow & { end_date: string };
			expect(body.status).toBe('active');
			expect(body.end_date).toBe('2031-06-30');
			expect(auditCount('contract.renewed', id)).toBe(1);
		} finally {
			if (id) deleteContract(id);
		}
	});
});

test.describe('contract RBAC', () => {
	test('clerk and cfo can read but cannot mutate the lifecycle', async ({
		page,
		tenantAdmin,
		tenantClerk,
		tenantCfo
	}) => {
		// Admin sets up an active contract.
		await apiSignIn(page, tenantAdmin);
		const vendor = await getFirstVendor(page);
		const number = `E2E-RBAC-${Date.now()}`;
		let id: string | null = null;
		try {
			const c = await createContract(page, {
				contract_number: number,
				vendor_id: vendor.id,
				contract_type: 'service',
				end_date: '2031-01-01'
			});
			id = c.id;
			await page.request.post(`${API_BASE}/api/contracts/${id}/activate`, {
				headers: await authedTenantHeaders(page)
			});

			for (const creds of [tenantClerk, tenantCfo]) {
				await apiSignIn(page, creds);
				const headers = await authedTenantHeaders(page);

				// Read is allowed for all four employee roles.
				const read = await page.request.get(`${API_BASE}/api/contracts/${id}`, { headers });
				expect(read.status()).toBe(200);

				// Every mutation is admin/ap_manager-only → 403.
				const terminate = await page.request.post(
					`${API_BASE}/api/contracts/${id}/terminate`,
					{ headers }
				);
				expect(terminate.status()).toBe(403);

				const renew = await page.request.post(`${API_BASE}/api/contracts/${id}/renew`, {
					headers,
					data: { end_date: '2032-01-01' }
				});
				expect(renew.status()).toBe(403);

				const createPo = await page.request.post(
					`${API_BASE}/api/contracts/${id}/create-po`,
					{ headers, data: {} }
				);
				expect(createPo.status()).toBe(403);

				const patch = await page.request.patch(`${API_BASE}/api/contracts/${id}`, {
					headers,
					data: { title: 'hijacked' }
				});
				expect(patch.status()).toBe(403);
			}

			// And the contract is untouched — still active, never terminated.
			await apiSignIn(page, tenantAdmin);
			expect((await getContract(page, id!)).status).toBe('active');
			expect(auditCount('contract.terminated', id!)).toBe(0);
		} finally {
			if (id) {
				await apiSignIn(page, tenantAdmin);
				deleteContract(id);
			}
		}
	});
});

test.describe('contract → PO creation', () => {
	test.beforeEach(async ({ page, tenantAdmin }) => {
		await apiSignIn(page, tenantAdmin);
	});

	test('create-po copies money + line items, links the PO, and audits', async ({ page }) => {
		const vendor = await getFirstVendor(page);
		const number = `E2E-PO-${Date.now()}`;
		let id: string | null = null;
		let poId: string | null = null;
		try {
			const c = await createContract(page, {
				contract_number: number,
				vendor_id: vendor.id,
				contract_type: 'purchase',
				end_date: '2031-01-01',
				line_items: [
					{ description: 'Widgets', quantity: '10', unit_price: '12.50', total: '125.00' },
					{ description: 'Gadgets', quantity: '4', unit_price: '93.75', total: '375.00' }
				]
			});
			id = c.id;

			const resp = await page.request.post(`${API_BASE}/api/contracts/${id}/create-po`, {
				headers: await authedTenantHeaders(page),
				data: {}
			});
			expect(resp.status()).toBe(201);
			const po = (await resp.json()) as {
				id: string;
				vendor_id: string;
				total: number;
				status: string;
				contract_id: string;
				line_items: Array<{ total: number | null }>;
			};
			poId = po.id;

			// PO points back at the contract, carries the contract's vendor, and
			// its total is the EXACT sum of line-item totals (125 + 375 = 500).
			expect(po.contract_id).toBe(id);
			expect(po.vendor_id).toBe(vendor.id);
			expect(po.total).toBe(500);
			expect(po.line_items.length).toBe(2);
			expect(po.status).toBe('open');

			// The DB stores it as Numeric (exact) — confirm no float drift.
			const dbTotal = tenantPsql(`SELECT total FROM purchase_orders WHERE id='${po.id}'`).trim();
			expect(dbTotal).toBe('500.00');

			// Audit row written against the CONTRACT entity.
			expect(auditCount('contract.po_created', id)).toBe(1);
		} finally {
			if (poId) {
				tenantPsql(`DELETE FROM po_line_items WHERE po_id='${poId}'`);
				tenantPsql(`DELETE FROM purchase_orders WHERE id='${poId}'`);
			}
			if (id) deleteContract(id);
		}
	});

	test('create-po is refused from a terminated contract (409)', async ({ page }) => {
		const vendor = await getFirstVendor(page);
		const number = `E2E-PO-TERM-${Date.now()}`;
		let id: string | null = null;
		try {
			const c = await createContract(page, {
				contract_number: number,
				vendor_id: vendor.id,
				contract_type: 'purchase',
				end_date: '2031-01-01'
			});
			id = c.id;
			await page.request.post(`${API_BASE}/api/contracts/${id}/activate`, {
				headers: await authedTenantHeaders(page)
			});
			await page.request.post(`${API_BASE}/api/contracts/${id}/terminate`, {
				headers: await authedTenantHeaders(page)
			});

			const resp = await page.request.post(`${API_BASE}/api/contracts/${id}/create-po`, {
				headers: await authedTenantHeaders(page),
				data: {}
			});
			expect(resp.status()).toBe(409);
			expect(auditCount('contract.po_created', id)).toBe(0);
			// No orphaned PO created.
			const poCount = tenantPsql(
				`SELECT count(*) FROM purchase_orders WHERE po_number LIKE 'PO-${number}-%'`
			).trim();
			expect(poCount).toBe('0');
		} finally {
			if (id) deleteContract(id);
		}
	});
});

test.describe('spend-to-contract attribution', () => {
	test.beforeEach(async ({ page, tenantAdmin }) => {
		await apiSignIn(page, tenantAdmin);
	});

	test('linked invoices roll up exactly; rejected excluded; over-limit math correct', async ({
		page
	}) => {
		const vendor = await getFirstVendor(page);
		const number = `E2E-SPEND-${Date.now()}`;
		let id: string | null = null;
		const invoiceIds: string[] = [];
		try {
			const c = await createContract(page, {
				contract_number: number,
				vendor_id: vendor.id,
				contract_type: 'service',
				currency: 'USD',
				spend_limit: '300.00',
				end_date: '2031-01-01'
			});
			id = c.id;

			// Empty contract: zeroed spend, remaining == limit, not over.
			const empty = await getContract(page, id);
			expect(empty.spend?.invoiced_total).toBe(0);
			expect(empty.spend?.invoice_count).toBe(0);
			expect(empty.spend?.remaining).toBe(300);
			expect(empty.spend?.over_limit).toBe(false);

			// Three invoices with decimal amounts that exercise rounding:
			// 100.10 + 100.20 = 200.30 counted; a rejected 999.99 excluded.
			// POST /api/invoices ignores a client-supplied status (status-injection
			// fix), so we set the intended status via SQL after each POST.
			const amounts: Array<{ amt: string; status: string }> = [
				{ amt: '100.10', status: 'new' },
				{ amt: '100.20', status: 'approved' },
				{ amt: '999.99', status: 'rejected' }
			];
			for (let i = 0; i < amounts.length; i++) {
				const { amt, status } = amounts[i];
				const r = await page.request.post(`${API_BASE}/api/invoices`, {
					headers: await authedTenantHeaders(page),
					data: {
						vendor: vendor.name,
						invoice_number: `SPEND-${Date.now()}-${i}`,
						amount: amt,
						currency: 'USD'
					}
				});
				expect(r.status()).toBe(201);
				const inv = (await r.json()) as { id: string };
				invoiceIds.push(inv.id);
				// Force the intended status — API always creates `new`.
				tenantPsql(`UPDATE invoices SET status='${status}' WHERE id='${inv.id}'`);
				// Link the invoice to the contract (spend attribution).
				const link = await page.request.post(
					`${API_BASE}/api/invoices/${inv.id}/link-contract`,
					{ headers: await authedTenantHeaders(page), data: { contract_id: id } }
				);
				expect(link.status()).toBe(200);
			}

			const after = await getContract(page, id);
			// Decimal-exact: 100.10 + 100.20 = 200.30, NOT 200.29999…
			expect(after.spend?.invoiced_total).toBe(200.3);
			// Rejected invoice excluded → count is 2, not 3.
			expect(after.spend?.invoice_count).toBe(2);
			// remaining = 300.00 - 200.30 = 99.70
			expect(after.spend?.remaining).toBe(99.7);
			expect(after.spend?.over_limit).toBe(false);

			// Push the 'new' invoice's amount over the limit and re-check.
			tenantPsql(`UPDATE invoices SET amount='250.00' WHERE id='${invoiceIds[0]}'`);
			const over = await getContract(page, id);
			// 250.00 + 100.20 = 350.20 > 300.00
			expect(over.spend?.invoiced_total).toBe(350.2);
			expect(over.spend?.remaining).toBe(-50.2);
			expect(over.spend?.over_limit).toBe(true);
		} finally {
			for (const iid of invoiceIds) deleteInvoice(iid);
			if (id) deleteContract(id);
		}
	});
});

test.describe('contract file download isolation', () => {
	test.beforeEach(async ({ page, tenantAdmin }) => {
		await apiSignIn(page, tenantAdmin);
	});

	test('a cross-org file key is refused (404, no enumeration)', async ({ page }) => {
		const headers = await authedTenantHeaders(page);
		// Worker tenant is e2e4 → org id ...e2e000000004. Forge a key whose
		// first segment is a DIFFERENT org (e2e3). The proxy's prefix check
		// must reject it with the same 404 it returns for a missing file, so
		// the response can't be used to enumerate other tenants' prefixes.
		const otherOrg = '00000000-0000-0000-0000-e2e000000003';
		const forged = `${otherOrg}/contracts/${crypto.randomUUID()}/secret.pdf`;
		const resp = await page.request.get(
			`${API_BASE}/api/contracts/file/${forged}`,
			{ headers }
		);
		expect(resp.status()).toBe(404);

		// A key under our OWN org but for a non-existent file is also 404 —
		// identical response, so wrong-org and missing-file are indistinguishable.
		const ownOrg = '00000000-0000-0000-0000-e2e000000004';
		const ownMissing = `${ownOrg}/contracts/${crypto.randomUUID()}/nope.pdf`;
		const own = await page.request.get(
			`${API_BASE}/api/contracts/file/${ownMissing}`,
			{ headers }
		);
		expect(own.status()).toBe(404);
	});
});
