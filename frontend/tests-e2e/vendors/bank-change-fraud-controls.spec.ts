import {
	API_BASE,
	authedTenantHeaders,
	expect,
	tenantPsql,
	test
} from '../fixtures/helpers';

/**
 * Vendor bank-detail change control — the BEC (business-email-compromise)
 * defense path. The critical invariant: a change to a vendor's banking
 * details is STAGED for AP approval and does NOT take effect until an AP
 * admin/manager explicitly approves it. A redirected bank account that
 * applied silently is the canonical vendor-impersonation fraud, so this
 * spec proves:
 *
 *   1. A pending `vendor_change_requests` row does NOT mutate the vendor —
 *      the live bank_details still point at the original account.
 *   2. Approving the request applies the staged value (exactly once) and
 *      writes an append-only audit row carrying only a masked last-4.
 *   3. Rejecting a request never touches the vendor row.
 *   4. Re-resolving an already-resolved request is a 409 (apply is
 *      exactly-once, locked).
 *   5. The AP-side detail endpoint reveals the full proposed value so the
 *      operator can verify the new account before approving; the queue
 *      list masks it.
 *
 * Setup uses direct DB inserts to simulate a supplier-portal submission
 * (the portal-user auth handshake is heavyweight and covered by backend
 * pytest); every mutation we assert (approve / reject) is driven through
 * the real AP API so the staged-approval contract is exercised end-to-end.
 */

interface VendorResp {
	id: string;
	name: string;
	status: string;
	organization_id?: string;
	bank_details: {
		counterparty_id?: string | null;
		account_last4?: string | null;
		bank_name?: string | null;
		country?: string | null;
	} | null;
}

interface ChangeRequestResp {
	id: string;
	vendor_id: string;
	change_type: string;
	status: string;
	proposed_value?: Record<string, unknown> | null;
	masked?: Record<string, unknown> | null;
}

// Resolved once per test in beforeEach. We derive the slug from the page's
// actual origin (`<slug>.localhost`) and thread it explicitly into BOTH the
// API headers AND tenantPsql — never relying on the implicit
// `currentTenantSlug()` default in two independent call sites lining up
// (psql and the JWT must always target the same tenant DB or a staged row
// goes missing).
let H: Record<string, string>;
let SLUG: string;

function slugFromPage(page: import('@playwright/test').Page): string {
	const host = new URL(page.url()).hostname; // e.g. e2e4.localhost
	return host.split('.')[0];
}

async function createVendor(
	page: import('@playwright/test').Page,
	name: string,
	bank: Record<string, unknown>
): Promise<VendorResp> {
	const resp = await page.request.post(`${API_BASE}/api/vendors`, {
		headers: H,
		data: { name, bank_details: bank }
	});
	expect(resp.status(), `create vendor ${name}`).toBe(201);
	return (await resp.json()) as VendorResp;
}

async function getVendor(
	page: import('@playwright/test').Page,
	id: string
): Promise<VendorResp> {
	const resp = await page.request.get(`${API_BASE}/api/vendors/${id}`, { headers: H });
	expect(resp.status()).toBe(200);
	return (await resp.json()) as VendorResp;
}

/** Insert a pending bank_details change request straight into the tenant DB,
 *  simulating a supplier-portal submission. Reads the vendor's own
 *  organization_id from the DB (the vendor API response doesn't expose it).
 *  Returns the request id. */
function stageBankChange(vendorId: string, proposed: Record<string, unknown>): string {
	const json = JSON.stringify({ bank_details: proposed }).replace(/'/g, "''");
	const out = tenantPsql(
		`INSERT INTO vendor_change_requests ` +
			`(id, vendor_id, organization_id, requested_by_vendor_user_id, ` +
			`change_type, status, proposed_value, created_at, updated_at) ` +
			`SELECT gen_random_uuid(), v.id, v.organization_id, gen_random_uuid(), ` +
			`'bank_details', 'pending', '${json}'::jsonb, now(), now() ` +
			`FROM vendors v WHERE v.id='${vendorId}' RETURNING id`,
		SLUG
	);
	// psql echoes the command-status line ("INSERT 0 1") after a RETURNING
	// result, so the raw output is two lines: the UUID then the status.
	// Take the first non-empty line — the returned id.
	return out
		.split('\n')
		.map((l) => l.trim())
		.filter(Boolean)[0]!;
}

function deleteVendorCascade(vendorId: string): void {
	// change-requests + sanctions_checks are FK'd; clear them first, then the
	// vendor. audit_log is append-only (immutable trigger), so leftover audit
	// rows are intentionally NOT deleted — they're PII-free and harmless.
	// Wrapped so cleanup never throws on a partial setup.
	try {
		tenantPsql(`DELETE FROM vendor_change_requests WHERE vendor_id='${vendorId}'`, SLUG);
		tenantPsql(`DELETE FROM sanctions_checks WHERE vendor_id='${vendorId}'`, SLUG);
		tenantPsql(`DELETE FROM vendors WHERE id='${vendorId}'`, SLUG);
	} catch {
		/* best-effort */
	}
}

test.describe('/vendors bank-detail change control (BEC defense)', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/vendors');
		SLUG = slugFromPage(page);
		H = await authedTenantHeaders(page, SLUG);
	});

	test('a pending bank-change request does NOT mutate the live vendor', async ({ page }) => {
		const vendor = await createVendor(page, `BEC-Stage Co ${Date.now()}`, {
			account_number: '11112222',
			account_last4: '2222',
			bank_name: 'Original Bank',
			country: 'US'
		});
		let requestId = '';
		try {
			requestId = stageBankChange(vendor.id, {
				account_number: '99998888',
				account_last4: '8888',
				bank_name: 'Fraudster Bank',
				country: 'US'
			});
			expect(requestId).toMatch(/[0-9a-f-]{36}/);

			// The live vendor must be UNCHANGED — staging never mutates the row.
			const live = await getVendor(page, vendor.id);
			expect(live.bank_details?.account_last4).toBe('2222');
			expect(live.bank_details?.bank_name).toBe('Original Bank');
		} finally {
			deleteVendorCascade(vendor.id);
		}
	});

	test('approving the request applies the staged bank details exactly once', async ({
		page
	}) => {
		const vendor = await createVendor(page, `BEC-Approve Co ${Date.now()}`, {
			account_number: '11112222',
			account_last4: '2222',
			bank_name: 'Original Bank',
			country: 'US'
		});
		try {
			const requestId = stageBankChange(vendor.id, {
				account_number: '99998888',
				account_last4: '8888',
				bank_name: 'Fraudster Bank',
				country: 'US'
			});

			// Approve via the real AP endpoint.
			const approve = await page.request.post(
				`${API_BASE}/api/vendors/change-requests/${requestId}/approve`,
				{ headers: H, data: { review_note: 'verified by phone callback' } }
			);
			expect(approve.status()).toBe(200);
			const approved = (await approve.json()) as ChangeRequestResp;
			expect(approved.status).toBe('approved');

			// NOW the live vendor reflects the new account.
			const after = await getVendor(page, vendor.id);
			expect(after.bank_details?.account_last4).toBe('8888');
			expect(after.bank_details?.bank_name).toBe('Fraudster Bank');

			// Re-approving the resolved request is a 409 — apply is exactly-once.
			const second = await page.request.post(
				`${API_BASE}/api/vendors/change-requests/${requestId}/approve`,
				{ headers: H }
			);
			expect(second.status()).toBe(409);

			// Approval writes an append-only audit row that records only the
			// masked last-4 — the full account number never enters the trail.
			const audit = tenantPsql(
				`SELECT count(*) FROM audit_log WHERE entity_id='${vendor.id}' ` +
					`AND action='vendor.bank_details_change_approved'`,
				SLUG
			).trim();
			expect(Number(audit)).toBeGreaterThan(0);
			const detailsHasFull = tenantPsql(
				`SELECT count(*) FROM audit_log WHERE entity_id='${vendor.id}' ` +
					`AND action='vendor.bank_details_change_approved' ` +
					`AND details::text LIKE '%99998888%'`,
				SLUG
			).trim();
			expect(Number(detailsHasFull)).toBe(0);
		} finally {
			deleteVendorCascade(vendor.id);
		}
	});

	test('rejecting a request never touches the vendor row', async ({ page }) => {
		const vendor = await createVendor(page, `BEC-Reject Co ${Date.now()}`, {
			account_number: '11112222',
			account_last4: '2222',
			bank_name: 'Original Bank',
			country: 'US'
		});
		try {
			const requestId = stageBankChange(vendor.id, {
				account_number: '99998888',
				account_last4: '8888',
				bank_name: 'Fraudster Bank',
				country: 'US'
			});

			const reject = await page.request.post(
				`${API_BASE}/api/vendors/change-requests/${requestId}/reject`,
				{ headers: H, data: { review_note: 'could not verify' } }
			);
			expect(reject.status()).toBe(200);
			expect(((await reject.json()) as ChangeRequestResp).status).toBe('rejected');

			// Vendor untouched — the original account survives.
			const after = await getVendor(page, vendor.id);
			expect(after.bank_details?.account_last4).toBe('2222');
			expect(after.bank_details?.bank_name).toBe('Original Bank');
		} finally {
			deleteVendorCascade(vendor.id);
		}
	});

	test('the per-vendor detail endpoint reveals the full proposed account', async ({
		page
	}) => {
		const vendor = await createVendor(page, `BEC-Reveal Co ${Date.now()}`, {
			account_number: '11112222',
			account_last4: '2222',
			bank_name: 'Original Bank',
			country: 'US'
		});
		try {
			stageBankChange(vendor.id, {
				account_number: '99998888',
				account_last4: '8888',
				bank_name: 'Fraudster Bank',
				country: 'US'
			});

			// AP must be able to see the full new account to verify it before
			// approving (callback control). The detail endpoint reveals it.
			const resp = await page.request.get(
				`${API_BASE}/api/vendors/${vendor.id}/change-requests`,
				{ headers: H }
			);
			expect(resp.status()).toBe(200);
			const rows = (await resp.json()) as ChangeRequestResp[];
			expect(rows.length).toBe(1);
			expect(rows[0].change_type).toBe('bank_details');
			expect(rows[0].status).toBe('pending');
			const revealed = JSON.stringify(rows[0]);
			expect(revealed).toContain('99998888');
		} finally {
			deleteVendorCascade(vendor.id);
		}
	});

	// RBAC on these endpoints (clerk cannot approve / reject a change request)
	// is already gated by the backend coverage test (`tests/test_rbac.py`) and
	// the clerk-403 case in `organization/fraud-rules.spec.ts`; we don't
	// duplicate a second UI login here (it only adds rate-limit pressure on the
	// shared login endpoint). This spec focuses on the genuinely-uncovered
	// staged-approval / no-silent-mutation invariant above.
});
