import { API_BASE, authedTenantHeaders, expect, tenantPsql, test } from '../fixtures/helpers';

/**
 * /requisitions?id=<uuid> deep-link.
 *
 * The punch-out flow (and any future caller) converts a returned supplier
 * cart into a draft requisition and then links the buyer straight to
 * `/requisitions?id=<requisition_id>`, expecting that requisition's detail
 * modal to open. Before the fix the requisitions page ignored the `id`
 * param, dropping the user on the unfiltered list — a dead-end deep-link.
 *
 * These tests lock the behaviour: the param opens the right requisition's
 * modal (resolved from the API, not just the in-memory list), closing the
 * modal scrubs `id` from the URL, and a bad id fails gracefully without a
 * stuck modal.
 */

const DETAIL_MODAL = 'div.modal[role="dialog"][aria-label="Requisition detail"]';

async function createRequisition(
	page: import('@playwright/test').Page
): Promise<{ id: string; requisition_number: string }> {
	const requisition_number = `RQ-DL-${Date.now()}`;
	const resp = await page.request.post(`${API_BASE}/api/requisitions`, {
		headers: await authedTenantHeaders(page),
		data: {
			requisition_number,
			title: 'Deep-link target',
			department: null,
			needed_by: null,
			justification: null,
			currency: 'USD',
			notes: null,
			line_items: [{ description: 'Widget', quantity: 2, unit_price: 5 }]
		}
	});
	expect(resp.status()).toBe(201);
	const body = (await resp.json()) as { id: string; requisition_number: string };
	return body;
}

function deleteRequisition(id: string): void {
	tenantPsql(`DELETE FROM requisition_line_items WHERE requisition_id='${id}'`);
	tenantPsql(`DELETE FROM purchase_requisitions WHERE id='${id}'`);
}

test.describe('/requisitions?id deep-link', () => {
	test('opens the detail modal for the linked requisition on load', async ({ page }) => {
		const req = await createRequisition(page);
		try {
			await page.goto(`/requisitions?id=${req.id}`);

			const modal = page.locator(DETAIL_MODAL);
			await expect(modal).toBeVisible();
			await expect(modal.locator('h2')).toContainText(req.requisition_number);
		} finally {
			deleteRequisition(req.id);
		}
	});

	test('the transient id is scrubbed from the URL and the modal closes cleanly', async ({
		page
	}) => {
		const req = await createRequisition(page);
		try {
			await page.goto(`/requisitions?id=${req.id}`);
			const modal = page.locator(DETAIL_MODAL);
			await expect(modal).toBeVisible();
			// `id` is a transient deep-link param — once consumed it is normalized
			// out of the URL, so a refresh / back doesn't re-open the modal.
			await expect(page).toHaveURL(/\/requisitions$/);

			await modal.getByRole('button', { name: 'Close' }).click();
			await expect(modal).toBeHidden();
			await expect(page).toHaveURL(/\/requisitions$/);
		} finally {
			deleteRequisition(req.id);
		}
	});

	test('a non-existent id does not strand a stuck modal', async ({ page }) => {
		await page.goto('/requisitions?id=00000000-0000-0000-0000-000000000000');
		// The list still renders; no detail modal hangs around.
		await expect(page.locator('table')).toBeVisible();
		await expect(page.locator(DETAIL_MODAL)).toHaveCount(0);
	});
});
