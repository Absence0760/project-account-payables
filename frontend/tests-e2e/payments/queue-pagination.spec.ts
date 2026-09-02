import type { Page } from '@playwright/test';

import { API_BASE, authedTenantHeaders, expect, tenantPsql, test } from '../fixtures/helpers';

/**
 * /payments Queue tab — server-side pagination + "Select all N matching".
 *
 * The queue used to load the tenant's whole approved-unpaid invoice set on
 * every view (issue #328). It now fetches one 20-row page with a Load-More
 * footer, and the pay-bar's "Select all N matching" resolves the whole
 * selectable set via `GET /api/payments/queue/ids` — so the pay-bar count
 * reflects the resolved set, not just the loaded rows.
 *
 * The seed already has a few payable invoices, so this spec adds a distinct
 * block that sorts deterministically first: each seeded row gets a far-past
 * `due_date` (the queue orders `due_date ASC NULLS LAST, id ASC`), so every
 * SEEDED-HERE row precedes every pre-existing queue row. All rows are
 * hard-deleted in a `finally`.
 */

const PAGE_SIZE = 20;
const SEEDED = 25; // one full page plus a tail
const PREFIX = 'QPAGE';
const VENDOR = 'E2E Queue Pagination Vendor';

async function seedApprovedInvoice(page: Page, invoiceNumber: string): Promise<string> {
	const resp = await page.request.post(`${API_BASE}/api/invoices`, {
		headers: await authedTenantHeaders(page),
		data: { vendor: VENDOR, invoice_number: invoiceNumber, amount: 100.0, currency: 'USD' }
	});
	expect(resp.status()).toBe(201);
	const { id } = (await resp.json()) as { id: string };
	const vendorId = tenantPsql(`SELECT id FROM vendors WHERE status='active' LIMIT 1`).trim();
	const sets = `status='approved', due_date='2000-01-01'${vendorId ? `, vendor_id='${vendorId}'` : ''}`;
	tenantPsql(`UPDATE invoices SET ${sets} WHERE id='${id}'`);
	return id;
}

function hardDeleteInvoice(id: string): void {
	tenantPsql(
		`DELETE FROM workflow_steps WHERE instance_id IN (SELECT id FROM workflow_instances WHERE invoice_id='${id}')`
	);
	tenantPsql(`DELETE FROM workflow_instances WHERE invoice_id='${id}'`);
	tenantPsql(`DELETE FROM exceptions WHERE invoice_id='${id}'`);
	tenantPsql(`DELETE FROM invoices WHERE id='${id}'`);
}

test.describe('/payments queue pagination', () => {
	test('page 1 caps at 20; select-all resolves the whole set; Load-More reveals the tail', async ({
		page
	}) => {
		const ids: string[] = [];
		try {
			for (let i = 0; i < SEEDED; i++) {
				ids.push(await seedApprovedInvoice(page, `${PREFIX}-${String(i).padStart(3, '0')}`));
			}

			await page.goto('/payments');
			await page.locator('.tab', { hasText: 'Queue' }).click();

			const rows = page.locator('table tbody tr');
			await expect(rows.first()).toBeVisible({ timeout: 10_000 });

			// 1. Page 1 is capped at the page size even though far more rows match.
			await expect(rows).toHaveCount(PAGE_SIZE);

			// 2. The footer names the server's whole-set total, which exceeds a page.
			const loadMore = page.locator('.btn-load-more');
			await expect(loadMore).toBeVisible();
			const footerTotal = Number((await loadMore.textContent())!.match(/of (\d+)/)![1]);
			expect(footerTotal).toBeGreaterThanOrEqual(SEEDED);

			// 3. "Select all N matching" resolves the WHOLE selectable set — not
			//    just the 20 loaded rows — and the pay-bar count reflects it.
			await page.locator('thead th.checkbox-col input[type="checkbox"]').check();
			const selectAll = page.locator('[data-testid="queue-select-all-matching"]');
			await expect(selectAll).toBeVisible();
			const offered = Number((await selectAll.textContent())!.match(/all (\d+) matching/)![1]);
			expect(offered).toBeGreaterThan(PAGE_SIZE); // there IS a gap past the page
			await selectAll.click();

			await expect(page.locator('[data-testid="queue-all-matching-note"]')).toBeVisible();
			const count = page.locator('[data-testid="pay-bar-count"]');
			await expect(count).toContainText(`${offered} selected`);

			// 4. Load-More appends the next page — the tail rows become reachable.
			await page.locator('.btn-clear').click();
			await expect(loadMore).toBeVisible();
			await loadMore.click();
			await expect(rows).toHaveCount(Math.min(PAGE_SIZE * 2, footerTotal));
			expect(await rows.count()).toBeGreaterThan(PAGE_SIZE);
		} finally {
			for (const id of ids) hardDeleteInvoice(id);
		}
	});
});
