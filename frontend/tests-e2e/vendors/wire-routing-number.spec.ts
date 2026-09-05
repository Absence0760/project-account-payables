import { API_BASE, authedTenantHeaders, expect, tenantPsql, test } from '../fixtures/helpers';

/**
 * Separate WIRE vs ACH routing numbers on a vendor's bank details.
 *
 * Larger US banks publish a different ABA for incoming Fedwires than for ACH,
 * so one generic `routing_number` cannot express a payable wire at those
 * banks. `bank_details` now carries `wire_routing_number` (+ its display
 * `wire_routing_last4`) alongside the original key, which keeps its existing
 * meaning — the ACH number.
 *
 * The load-bearing property is NOT that the field exists: it is that the new
 * field travels the SAME dual-control (BEC) gate as every other banking
 * field. A field that applied inline would be the one-person bank redirect
 * the gate exists to prevent. So this spec drives the real AP dialog and
 * asserts the vendor row is untouched afterwards.
 */

interface VendorResp {
	id: string;
	name: string;
	bank_details: {
		routing_last4?: string | null;
		wire_routing_last4?: string | null;
		bank_name?: string | null;
		country?: string | null;
	} | null;
}

let H: Record<string, string>;
let SLUG: string;

function slugFromPage(page: import('@playwright/test').Page): string {
	return new URL(page.url()).hostname.split('.')[0];
}

function deleteVendorCascade(vendorId: string): void {
	try {
		tenantPsql(`DELETE FROM vendor_change_requests WHERE vendor_id='${vendorId}'`, SLUG);
		tenantPsql(`DELETE FROM sanctions_checks WHERE vendor_id='${vendorId}'`, SLUG);
		tenantPsql(`DELETE FROM vendors WHERE id='${vendorId}'`, SLUG);
	} catch {
		/* best-effort */
	}
}

test.describe('vendor bank details — wire vs ACH routing number', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/vendors');
		SLUG = slugFromPage(page);
		H = await authedTenantHeaders(page, SLUG);
	});

	test('the AP bank dialog collects both numbers and STAGES the change', async ({ page }) => {
		const name = `Wire Routing Co ${Date.now()}`;
		// Created with no bank_details — `POST /api/vendors` would stage those
		// too, and the staging path is what this test drives through the UI.
		const created = await page.request.post(`${API_BASE}/api/vendors`, {
			headers: H,
			data: { name }
		});
		expect(created.status()).toBe(201);
		const vendor = (await created.json()) as VendorResp;

		try {
			await page.goto('/vendors?search=Wire+Routing+Co');
			const row = page.locator('.grid-container tbody tr', { hasText: name });
			await expect(row).toHaveCount(1);
			await row.getByRole('button', { name: /^Bank/ }).click();

			const dialog = page.getByRole('dialog', { name: 'Vendor bank counterparty' });
			await expect(dialog).toBeVisible();

			// Both routing fields are present and distinguishable — the whole
			// point is that a user can tell which number they are entering.
			const ach = dialog.locator('label', { hasText: 'ACH routing last 4' }).locator('input');
			const wire = dialog.locator('label', { hasText: 'Wire routing last 4' }).locator('input');
			await expect(ach).toBeVisible();
			await expect(wire).toBeVisible();

			await ach.fill('0021');
			await wire.fill('9593');
			await dialog.getByRole('button', { name: /Save/ }).click();

			// The dialog says where the change went; it did NOT apply.
			await expect(page.locator('.toast', { hasText: /Bank change approvals/i })).toBeVisible();

			const after = (await (
				await page.request.get(`${API_BASE}/api/vendors/${vendor.id}`, { headers: H })
			).json()) as VendorResp;
			expect(after.bank_details?.wire_routing_last4 ?? null).toBeNull();
			expect(after.bank_details?.routing_last4 ?? null).toBeNull();

			// It is waiting in the dual-control queue instead.
			const pending = tenantPsql(
				`SELECT proposed_value->'bank_details'->>'wire_routing_last4' ` +
					`FROM vendor_change_requests ` +
					`WHERE vendor_id='${vendor.id}' AND status='pending'`,
				SLUG
			);
			expect(pending).toContain('9593');
		} finally {
			deleteVendorCascade(vendor.id);
		}
	});

	test('a second approver applies both numbers onto the vendor', async ({ page }) => {
		const name = `Wire Approve Co ${Date.now()}`;
		const created = await page.request.post(`${API_BASE}/api/vendors`, {
			headers: H,
			data: { name }
		});
		expect(created.status()).toBe(201);
		const vendor = (await created.json()) as VendorResp;

		try {
			// Staged as the supplier portal writes one (no AP requester), so the
			// approve path's proposer-segregation check doesn't refuse our admin.
			const proposed = JSON.stringify({
				bank_details: {
					account_number: '12345678',
					routing_number: '021000021',
					wire_routing_number: '026009593'
				}
			}).replace(/'/g, "''");
			const requestId = tenantPsql(
				`INSERT INTO vendor_change_requests ` +
					`(id, vendor_id, organization_id, requested_by_vendor_user_id, ` +
					`change_type, status, proposed_value, created_at, updated_at) ` +
					`SELECT gen_random_uuid(), v.id, v.organization_id, gen_random_uuid(), ` +
					`'bank_details', 'pending', '${proposed}'::jsonb, now(), now() ` +
					`FROM vendors v WHERE v.id='${vendor.id}' RETURNING id`,
				SLUG
			)
				.split('\n')
				.map((l) => l.trim())
				.filter(Boolean)[0]!;
			expect(requestId).toMatch(/[0-9a-f-]{36}/);

			const approved = await page.request.post(
				`${API_BASE}/api/vendors/change-requests/${requestId}/approve`,
				{ headers: H, data: {} }
			);
			expect(approved.status(), await approved.text()).toBe(200);

			// Both survive onto the row, under their own keys.
			const stored = tenantPsql(
				`SELECT bank_details->>'routing_number', bank_details->>'wire_routing_number' ` +
					`FROM vendors WHERE id='${vendor.id}'`,
				SLUG
			);
			expect(stored).toContain('021000021');
			expect(stored).toContain('026009593');
		} finally {
			deleteVendorCascade(vendor.id);
		}
	});

	test('a malformed wire ABA is refused, and the response never echoes it', async ({ page }) => {
		const name = `Wire Reject Co ${Date.now()}`;
		const created = await page.request.post(`${API_BASE}/api/vendors`, {
			headers: H,
			data: { name }
		});
		expect(created.status()).toBe(201);
		const vendor = (await created.json()) as VendorResp;

		try {
			const resp = await page.request.post(`${API_BASE}/api/vendors/${vendor.id}/bank-change`, {
				headers: H,
				data: {
					bank_details: {
						account_number: '12345678',
						// One digit off — fails the ABA checksum.
						wire_routing_number: '021000020'
					}
				}
			});
			expect(resp.status()).toBe(422);
			const body = await resp.text();
			// The FIELD may be named; the submitted banking data must not be —
			// a 422 body carrying the account number is a PII leak, which is
			// why this check does not live in a Pydantic field validator.
			expect(body).toContain('wire_routing_number');
			expect(body).not.toContain('021000020');
			expect(body).not.toContain('12345678');

			// And nothing was staged.
			const staged = tenantPsql(
				`SELECT count(*) FROM vendor_change_requests WHERE vendor_id='${vendor.id}'`,
				SLUG
			);
			expect(staged).toContain('0');
		} finally {
			deleteVendorCascade(vendor.id);
		}
	});
});
