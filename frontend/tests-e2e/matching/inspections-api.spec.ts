import { API_BASE, authToken, expect, signInAndWait, tenantHeaders, test } from '../fixtures/helpers';
import { cleanup, createGr, createPo } from './setup';

/**
 * /api/inspections — the CRUD surface that feeds the 4-way leg.
 *
 * `po_matching` reads the rows this router creates, so its validation + RBAC
 * are part of the matching control path. Covers: create (201) + list + detail
 * round-trip, the result-enum 400, the bad-uuid 400, the create RBAC gate
 * (clerk denied), and that a created inspection is tied to its GR.
 *
 * The list is a PAGE (`{items, total, page, page_size}`) and takes a `gr_id`
 * filter; a row carries its receipt's `gr_number`, so the UI no longer fetches a
 * second list of receipts just to label a column.
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

		// Listed — the canonical paginated envelope, newest first.
		const list = await page.request.get(`${API_BASE}/api/inspections`, {
			headers: tenantHeaders(await authToken(page))
		});
		expect(list.status()).toBe(200);
		const listed = (await list.json()) as {
			items: Array<{ inspection_number: string; gr_number: string | null }>;
			total: number;
			page: number;
			page_size: number;
		};
		expect(listed.page).toBe(1);
		expect(listed.page_size).toBe(20);
		expect(listed.total).toBeGreaterThanOrEqual(1);
		expect(listed.items.map((i) => i.inspection_number)).toContain(number);
	});

	test('a row names its goods receipt, so the UI needs no second list to label it', async ({
		page
	}) => {
		const headers = {
			...tenantHeaders(await authToken(page)),
			'Content-Type': 'application/json'
		};
		const number = `QI-GRNUM-${Date.now().toString(36)}`;
		const create = await page.request.post(`${API_BASE}/api/inspections`, {
			headers,
			data: { inspection_number: number, gr_id: grId, result: 'pass' }
		});
		expect(create.status()).toBe(201);
		// The create response already carries it — a client rendering the row it
		// just wrote must not show a blank cell that fills in on the next load.
		const created = (await create.json()) as { id: string; gr_number: string | null };
		expect(created.gr_number).toBeTruthy();

		const detail = await page.request.get(`${API_BASE}/api/inspections/${created.id}`, {
			headers: tenantHeaders(await authToken(page))
		});
		expect(((await detail.json()) as { gr_number: string }).gr_number).toBe(created.gr_number);
	});

	test('?gr_id= narrows the list to one receipt, count included', async ({ page }) => {
		const headers = {
			...tenantHeaders(await authToken(page)),
			'Content-Type': 'application/json'
		};
		// A second receipt, so "narrowed" is a claim with something to exclude.
		const otherPo = createPo({
			total: 500,
			lines: [{ description: 'other', quantity: 1, unitPrice: 500, total: 500 }]
		});
		created.poIds.push(otherPo.poId);
		const otherGr = createGr({
			poId: otherPo.poId,
			lines: [{ description: 'other', quantityReceived: 1 }]
		});
		created.grIds.push(otherGr.grId);

		const stamp = Date.now().toString(36);
		const mine = `QI-FILT-MINE-${stamp}`;
		const other = `QI-FILT-OTHER-${stamp}`;
		for (const [num, gr] of [
			[mine, grId],
			[other, otherGr.grId]
		] as const) {
			const r = await page.request.post(`${API_BASE}/api/inspections`, {
				headers,
				data: { inspection_number: num, gr_id: gr, result: 'pass' }
			});
			expect(r.status()).toBe(201);
		}

		const filtered = await page.request.get(`${API_BASE}/api/inspections?gr_id=${grId}`, {
			headers: tenantHeaders(await authToken(page))
		});
		expect(filtered.status()).toBe(200);
		const body = (await filtered.json()) as {
			items: Array<{ inspection_number: string; gr_id: string }>;
			total: number;
		};
		const numbers = body.items.map((i) => i.inspection_number);
		expect(numbers).toContain(mine);
		expect(numbers).not.toContain(other);
		// The count describes the FILTERED set, not the tenant.
		expect(body.total).toBe(body.items.length);
		expect(body.items.every((i) => i.gr_id === grId)).toBe(true);
	});

	test('the list is a page, and page_size is capped server-side', async ({ page }) => {
		const headers = tenantHeaders(await authToken(page));
		const first = await page.request.get(`${API_BASE}/api/inspections?page=1&page_size=1`, {
			headers
		});
		expect(first.status()).toBe(200);
		const body = (await first.json()) as { items: unknown[]; total: number; page_size: number };
		expect(body.page_size).toBe(1);
		expect(body.items.length).toBeLessThanOrEqual(1);
		// `total` spans the whole set behind the page.
		expect(body.total).toBeGreaterThanOrEqual(body.items.length);

		// Asking for more than the cap is refused, not quietly granted — raising
		// page_size must not be a way around paging.
		const over = await page.request.get(`${API_BASE}/api/inspections?page_size=5000`, { headers });
		expect(over.status()).toBe(422);
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
