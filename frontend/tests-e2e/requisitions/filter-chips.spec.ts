import { API_BASE, authedTenantHeaders, expect, tenantPsql, test } from '../fixtures/helpers';

/**
 * /requisitions status filter chips — only reachable statuses are offered.
 *
 * `RequisitionStatus` carries `submitted`, but no backend transition ever
 * assigns it: `submit_requisition` jumps straight `draft → pending_approval`
 * (the module docstring in `api/requisitions.py` still advertises the older
 * two-step graph). The chip therefore returned an empty list forever and is
 * gone from the chip row.
 *
 * The value stays in the type union and the label map because it is still a
 * legal SOURCE state for legacy rows — `requisition_service.VALID_TRANSITIONS`
 * lets a `submitted` row move on or cancel, and `budget_service` counts it as
 * committed spend. So the page also follows the /invoices
 * `quick subset ∪ active` rule: an actively filtered status is appended to the
 * chip row, and such rows render their badge normally.
 */

const CHIPS = '.filter-row .filter-chip';

const REACHABLE = [
	'All',
	'Draft',
	'Pending Approval',
	'Approved',
	'Rejected',
	'Converted',
	'Cancelled'
];

async function createRequisition(
	page: import('@playwright/test').Page
): Promise<{ id: string; requisition_number: string }> {
	const requisition_number = `RQ-CHIP-${Date.now()}`;
	const resp = await page.request.post(`${API_BASE}/api/requisitions`, {
		headers: await authedTenantHeaders(page),
		data: {
			requisition_number,
			title: 'Chip-row fixture',
			department: null,
			needed_by: null,
			justification: null,
			currency: 'USD',
			notes: null,
			line_items: [{ description: 'Widget', quantity: 1, unit_price: 9 }]
		}
	});
	expect(resp.status()).toBe(201);
	return (await resp.json()) as { id: string; requisition_number: string };
}

function deleteRequisition(id: string): void {
	tenantPsql(`DELETE FROM requisition_line_items WHERE requisition_id='${id}'`);
	tenantPsql(`DELETE FROM purchase_requisitions WHERE id='${id}'`);
}

test.describe('/requisitions status chips', () => {
	test('offers only the statuses the backend can actually stamp', async ({ page }) => {
		await page.goto('/requisitions');
		await expect(page.locator('table')).toBeVisible();

		// `Submitted` is deliberately absent — submit goes straight to
		// Pending Approval, so nothing ever lands in it.
		await expect(page.locator(CHIPS)).toHaveText(REACHABLE);
	});

	test('an actively filtered unreachable status is still shown as a chip', async ({ page }) => {
		await page.goto('/requisitions?status=submitted');
		await expect(page.locator('table')).toBeVisible();

		await expect(page.locator(CHIPS)).toHaveText([...REACHABLE, 'Submitted']);
		const active = page.locator(`${CHIPS}[aria-pressed="true"]`);
		await expect(active).toHaveText('Submitted');

		await page.locator(CHIPS, { hasText: /^All$/ }).click();
		await expect(page.locator(CHIPS)).toHaveText(REACHABLE);
		await expect(page).toHaveURL(/\/requisitions$/);
	});

	test('a legacy row carrying an unreachable status still renders its badge', async ({ page }) => {
		const req = await createRequisition(page);
		try {
			// No API path produces this state — that is the whole point of the
			// chip removal — so the row is aged into it directly, the same way a
			// pre-existing tenant row would already be sitting there.
			tenantPsql(`UPDATE purchase_requisitions SET status='submitted' WHERE id='${req.id}'`);

			await page.goto('/requisitions?status=submitted');
			const row = page.locator('table tbody tr.clickable', {
				hasText: req.requisition_number
			});
			await expect(row).toHaveCount(1);
			await expect(row).toContainText('Submitted');
		} finally {
			deleteRequisition(req.id);
		}
	});
});
