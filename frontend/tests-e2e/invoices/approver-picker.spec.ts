import type { Page } from '@playwright/test';

import {
	API_BASE,
	authedTenantHeaders,
	expect,
	signInAndWait,
	tenantPsql,
	test
} from '../fixtures/helpers';

/**
 * `/invoices` — a non-admin must never be dead-ended by the approver picker.
 *
 * Regression: the picker sourced its options from `GET /api/admin/users`, which
 * is `require_roles(ROLE_ADMIN)`. For every non-admin that call 403'd, nothing
 * caught the rejection, `adminStore.users` stayed `[]`, the `<select>` rendered
 * only its placeholder — and Submit was `disabled={… || (needsApproverSelect &&
 * !selectedApproverId)}`, i.e. disabled forever with nothing selectable.
 *
 * The seeded workflow's approval step is `approver_strategy: "manual"`
 * (`backend/scripts/seed.py`), so `needsApproverSelect` is true for every `new`
 * invoice — and `POST /api/invoices/{id}/complete` allows admin, ap_manager AND
 * cfo. An ap_manager could therefore submit via the API but not via the UI.
 *
 * The durable fix is a non-admin-readable reviewer list
 * (`GET /api/invoices/assignable-reviewers`); the fix that makes the dead end
 * impossible *by construction* is that Submit only waits on the picker when the
 * picker actually has someone to pick. That second half is what these specs
 * lock, and it holds both before and after the endpoint lands.
 *
 * What "submit unassigned" means is checked against the backend, not assumed:
 * `complete_invoice` transitions to `ready_for_review` with no assignee, and an
 * unassigned invoice is reviewable by any approver (`canReview` in the modal
 * gates on `!invoice.assigned_to_id`).
 */

const SUBMIT = 'Submit for Review';

async function createNewInvoice(page: Page, invoiceNumber: string): Promise<string> {
	const res = await page.request.post(`${API_BASE}/api/invoices`, {
		headers: await authedTenantHeaders(page),
		data: {
			vendor: 'E2E Approver Picker Vendor',
			invoice_number: invoiceNumber,
			amount: '42.50',
			currency: 'USD'
		}
	});
	expect(res.ok()).toBeTruthy();
	return ((await res.json()) as { id: string }).id;
}

/**
 * Which approver affordance the modal is going to render for THIS tenant.
 *
 * `InvoiceModal.svelte` decides with two deriveds:
 *   needsApproverSelect = status 'new' && activeSteps.approval &&
 *                         approval_config.approver_strategy === 'manual'
 *   reviewerOptions     = assignable reviewers, minus the signed-in user
 *
 * so there are three outcomes, not two — and the third ("this workflow picks
 * its own approver") renders neither a picker nor a note. Re-derive them here
 * from the same endpoints, BEFORE the modal opens, so the assertions below are
 * a statement about what must happen rather than a guess about what did.
 */
async function expectedApproverWorld(page: Page): Promise<'picker' | 'note' | 'not-required'> {
	const headers = await authedTenantHeaders(page);
	const get = async <T>(path: string): Promise<T> => {
		const res = await page.request.get(`${API_BASE}${path}`, { headers });
		expect(res.ok(), `${path} must be readable by an ap_manager`).toBeTruthy();
		return (await res.json()) as T;
	};

	const steps = await get<{
		approval?: boolean;
		approval_config?: { approver_strategy?: string } | null;
	}>('/api/workflows/active/steps');
	if (!steps.approval || steps.approval_config?.approver_strategy !== 'manual') {
		return 'not-required';
	}

	const me = await get<{ id: string }>('/api/auth/me');
	const reviewers = await get<{ id: string; is_active: boolean }[]>(
		'/api/invoices/assignable-reviewers'
	);
	return reviewers.some((r) => r.is_active && r.id !== me.id) ? 'picker' : 'note';
}

/** Search the list down to one invoice number and open its detail modal. */
async function openInvoice(page: Page, invoiceNumber: string) {
	const listed = page.waitForResponse(
		(r) =>
			r.url().includes('/api/invoices?') &&
			r.url().includes(`search=${encodeURIComponent(invoiceNumber)}`) &&
			r.request().method() === 'GET'
	);
	await page.getByPlaceholder('Search invoices...').fill(invoiceNumber);
	await listed;

	const row = page.locator('table tbody tr', { hasText: invoiceNumber }).first();
	await expect(row).toBeVisible();
	await row.click();
	await expect(page.getByRole('button', { name: SUBMIT })).toBeVisible();
}

function cleanUp(id: string | null) {
	if (!id) return;
	tenantPsql(
		`DELETE FROM workflow_steps WHERE instance_id IN (SELECT id FROM workflow_instances WHERE invoice_id='${id}')`
	);
	tenantPsql(`DELETE FROM workflow_instances WHERE invoice_id='${id}'`);
	tenantPsql(`DELETE FROM exceptions WHERE invoice_id='${id}'`);
	tenantPsql(`DELETE FROM invoices WHERE id='${id}'`);
}

test.describe('/invoices approver picker — non-admin', () => {
	let invoiceId: string | null = null;
	test.afterEach(() => {
		cleanUp(invoiceId);
		invoiceId = null;
	});

	test('an ap_manager can always reach a submittable state', async ({ page, tenantManager }) => {
		await signInAndWait(page, tenantManager);
		const number = `E2E-APPROVER-${Date.now()}`;
		invoiceId = await createNewInvoice(page, number);

		// Decide WHICH world this tenant is in before opening the modal, from the
		// same two endpoints the modal reads. Probing the rendered picker instead
		// (`picker.isVisible()`) can't work: that check does not retry, the picker
		// only exists once `GET /assignable-reviewers` has resolved, and the modal
		// paints its Submit button long before that — so a tenant that HAS
		// reviewers reads as "no picker" and the spec then waits forever for a
		// note the app is right not to render.
		const world = await expectedApproverWorld(page);

		await page.goto('/invoices');
		await openInvoice(page, number);

		const submit = page.getByRole('button', { name: SUBMIT });
		const picker = page.getByLabel('Assign approver');
		const note = page.locator('.approver-note');

		// Three legitimate worlds, one invariant: the manager is never stuck.
		if (world === 'picker') {
			// Reviewers exist: pick one. Assert the picker FIRST — Submit is only
			// disabled once the list has landed, and `approverRequired` is derived
			// from the very same state that renders the `<select>`.
			await expect(picker).toBeVisible();
			await expect(submit).toBeDisabled();
			// The placeholder plus at least one real reviewer.
			expect(await picker.locator('option').count()).toBeGreaterThan(1);
			await picker.selectOption({ index: 1 });
			await expect(submit).toBeEnabled();
		} else if (world === 'note') {
			// The approval step wants an approver but nobody can be offered.
			// Submit must stand on its own with the consequence spelled out.
			await expect(note).toBeVisible();
			await expect(picker).toHaveCount(0);
			await expect(submit).toBeEnabled();
		} else {
			// This tenant's approval step assigns its own approver (`specific` /
			// `auto`), so there is nothing to pick and nothing to explain. The
			// modal renders neither control — which means the ONLY way to show the
			// manager isn't stuck is to actually submit and watch it move.
			await expect(picker).toHaveCount(0);
			await expect(note).toHaveCount(0);
			await expect(submit).toBeEnabled();
			await submit.click();
			await expect
				.poll(async () => {
					const res = await page.request.get(`${API_BASE}/api/invoices/${invoiceId}`, {
						headers: await authedTenantHeaders(page)
					});
					if (!res.ok()) return null;
					return ((await res.json()) as { status: string }).status;
				})
				.not.toBe('new');
		}
	});

	test('a failed reviewer lookup explains itself and leaves Submit usable', async ({
		page,
		tenantManager
	}) => {
		await signInAndWait(page, tenantManager);
		const number = `E2E-APPROVER-FAIL-${Date.now()}`;
		invoiceId = await createNewInvoice(page, number);

		// Force the failure deterministically on BOTH sources, so this spec means
		// the same thing before and after the reviewer endpoint ships.
		for (const path of ['/api/invoices/assignable-reviewers', '/api/admin/users']) {
			await page.route(
				(url) => url.pathname === path,
				(route) =>
					route.fulfill({
						status: 403,
						contentType: 'application/json',
						body: JSON.stringify({ detail: 'Insufficient permissions' })
					})
			);
		}

		await page.goto('/invoices');
		await openInvoice(page, number);

		// No picker, an explanation, and a Submit that works — the whole point.
		await expect(page.getByLabel('Assign approver')).toHaveCount(0);
		await expect(page.locator('.approver-note')).toBeVisible();
		await expect(page.getByRole('button', { name: SUBMIT })).toBeEnabled();
	});

	test('submitting without an approver lands the invoice in the review queue', async ({
		page,
		tenantManager
	}) => {
		await signInAndWait(page, tenantManager);
		const number = `E2E-APPROVER-SUBMIT-${Date.now()}`;
		invoiceId = await createNewInvoice(page, number);

		for (const path of ['/api/invoices/assignable-reviewers', '/api/admin/users']) {
			await page.route(
				(url) => url.pathname === path,
				(route) =>
					route.fulfill({
						status: 403,
						contentType: 'application/json',
						body: JSON.stringify({ detail: 'Insufficient permissions' })
					})
			);
		}

		await page.goto('/invoices');
		await openInvoice(page, number);
		await page.getByRole('button', { name: SUBMIT }).click();

		// The note promises the review queue with no assignee — verify the claim
		// against the server rather than the toast.
		await expect
			.poll(
				async () => {
					const res = await page.request.get(`${API_BASE}/api/invoices/${invoiceId}`, {
						headers: await authedTenantHeaders(page)
					});
					if (!res.ok()) return null;
					const inv = (await res.json()) as {
						status: string;
						assigned_to_id: string | null;
					};
					return inv.status;
				},
				{ timeout: 15_000 }
			)
			.toBe('ready_for_review');

		const res = await page.request.get(`${API_BASE}/api/invoices/${invoiceId}`, {
			headers: await authedTenantHeaders(page)
		});
		const inv = (await res.json()) as { assigned_to_id: string | null };
		expect(inv.assigned_to_id).toBeFalsy();
	});
});

test.describe('/invoices approver picker — admin', () => {
	let invoiceId: string | null = null;
	test.afterEach(() => {
		cleanUp(invoiceId);
		invoiceId = null;
	});

	test('an admin still gets a populated picker when only the new endpoint is missing', async ({
		page
	}) => {
		const number = `E2E-APPROVER-ADMIN-${Date.now()}`;
		invoiceId = await createNewInvoice(page, number);

		// Simulate a backend that predates the reviewer endpoint. The admin
		// fallback onto `GET /api/admin/users` must keep the picker working —
		// the fix must not regress the role that already had it.
		await page.route(
			(url) => url.pathname === '/api/invoices/assignable-reviewers',
			(route) =>
				route.fulfill({
					status: 404,
					contentType: 'application/json',
					body: JSON.stringify({ detail: 'Not Found' })
				})
		);

		await page.goto('/invoices');
		await openInvoice(page, number);

		const picker = page.getByLabel('Assign approver');
		await expect(picker).toBeVisible();
		expect(await picker.locator('option').count()).toBeGreaterThan(1);
		await expect(page.getByRole('button', { name: SUBMIT })).toBeDisabled();
	});
});
