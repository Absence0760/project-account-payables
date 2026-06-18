import { API_BASE, authToken, expect, signInAndWait, tenantHeaders, test } from '../fixtures/helpers';
import { cleanup, createGr, createPo } from './setup';

/**
 * /api/inspections — the CRUD surface that feeds the 4-way leg.
 *
 * `po_matching` reads the rows this router creates, so its validation + RBAC
 * are part of the matching control path. Covers: create (201) + list + detail
 * round-trip, the result-enum 400, the bad-uuid 400, the create RBAC gate
 * (clerk denied), and that a created inspection is tied to its GR.
 */

test.describe('/api/inspections CRUD + RBAC', () => {
	const created: { grIds: string[]; poIds: string[] } = { grIds: [], poIds: [] };
	let poId: string;
	let grId: string;

	test.beforeAll(() => {
		const po = createPo({
			total: 1000,
			lines: [{ description: 'part', quantity: 5, unitPrice: 200, total: 1000 }]
		});
		poId = po.poId;
		created.poIds.push(poId);
		const gr = createGr({ poId, lines: [{ description: 'part', quantityReceived: 5 }] });
		grId = gr.grId;
		created.grIds.push(grId);
	});

	test.afterAll(() => cleanup(created));

	test('admin can create, then list + fetch it back', async ({ page }) => {
		const headers = {
			...tenantHeaders(await authToken(page)),
			'Content-Type': 'application/json'
		};
		const number = `QI-API-${Date.now().toString(36)}`;
		const create = await page.request.post(`${API_BASE}/api/inspections`, {
			headers,
			data: {
				inspection_number: number,
				gr_id: grId,
				po_id: poId,
				result: 'partial',
				accepted_quantity: 4,
				rejected_quantity: 1,
				deviation_notes: 'one short'
			}
		});
		expect(create.status()).toBe(201);
		const body = (await create.json()) as {
			id: string;
			result: string;
			gr_id: string;
			po_id: string;
			accepted_quantity: number;
		};
		expect(body.result).toBe('partial');
		expect(body.gr_id).toBe(grId);
		expect(body.po_id).toBe(poId);
		expect(body.accepted_quantity).toBeCloseTo(4, 4);

		// Detail round-trip.
		const detail = await page.request.get(`${API_BASE}/api/inspections/${body.id}`, {
			headers: tenantHeaders(await authToken(page))
		});
		expect(detail.status()).toBe(200);
		expect(((await detail.json()) as { inspection_number: string }).inspection_number).toBe(number);

		// Listed.
		const list = await page.request.get(`${API_BASE}/api/inspections`, {
			headers: tenantHeaders(await authToken(page))
		});
		expect(list.status()).toBe(200);
		const numbers = ((await list.json()) as Array<{ inspection_number: string }>).map(
			(i) => i.inspection_number
		);
		expect(numbers).toContain(number);
	});

	test('an invalid result value is a clean 400', async ({ page }) => {
		const headers = {
			...tenantHeaders(await authToken(page)),
			'Content-Type': 'application/json'
		};
		const resp = await page.request.post(`${API_BASE}/api/inspections`, {
			headers,
			data: { inspection_number: 'QI-BAD-RESULT', gr_id: grId, result: 'maybe' }
		});
		expect(resp.status()).toBe(400);
		expect(((await resp.json()) as { detail: string }).detail).toMatch(/result must be one of/i);
	});

	test('a malformed gr_id is a clean 400', async ({ page }) => {
		const headers = {
			...tenantHeaders(await authToken(page)),
			'Content-Type': 'application/json'
		};
		const resp = await page.request.post(`${API_BASE}/api/inspections`, {
			headers,
			data: { inspection_number: 'QI-BAD-GR', gr_id: 'not-a-uuid', result: 'pass' }
		});
		expect(resp.status()).toBe(400);
		expect(((await resp.json()) as { detail: string }).detail).toMatch(/invalid gr_id/i);
	});

	test('detail for an unknown inspection id is 404', async ({ page }) => {
		const resp = await page.request.get(
			`${API_BASE}/api/inspections/00000000-0000-0000-0000-000000000000`,
			{ headers: tenantHeaders(await authToken(page)) }
		);
		expect(resp.status()).toBe(404);
	});

	test('a clerk cannot create an inspection (admin/ap_manager only)', async ({
		page,
		tenantClerk
	}) => {
		await signInAndWait(page, tenantClerk);
		const headers = {
			...tenantHeaders(await authToken(page)),
			'Content-Type': 'application/json'
		};
		const resp = await page.request.post(`${API_BASE}/api/inspections`, {
			headers,
			data: { inspection_number: 'QI-CLERK', gr_id: grId, result: 'pass' }
		});
		expect(resp.status()).toBe(403);
	});
});
