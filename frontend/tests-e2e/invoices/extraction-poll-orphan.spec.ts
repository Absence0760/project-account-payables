import type { Page } from '@playwright/test';

import { API_BASE, authedTenantHeaders, deleteInvoicesWhere, expect, tenantPsql, test } from '../fixtures/helpers';

/**
 * `/invoices` — closing the modal mid-extraction must stop the poll.
 *
 * Regression: `InvoiceModal.pollForCompletion` runs for up to 60 s (30 ticks ×
 * 2 s) and nothing disables Close while it does, so the loop routinely outlived
 * its own component. When it did, it kept polling a modal nobody could see and
 * then called the store's UNFILTERED `invoiceStore.fetch()`. The host had
 * already re-applied its filters on close, so seconds later the list silently
 * widened to every status while the chips still claimed a filter — and the
 * store's `lastParams` reset, so the next Load-more paged a different set. The
 * approve path avoided this hazard explicitly (see the comment in
 * `handleApprove`); the extraction poll did not.
 *
 * The fix is an `$effect` teardown flipping `pollCancelled`, re-checked after
 * every `await` in the poll, plus routing the refresh through a host-supplied
 * `onrefresh` callback that carries the page's own filters.
 *
 * The extraction itself is stubbed (the POST is fulfilled, and the detail GET
 * always answers `pending`) so the poll is held open deterministically — the
 * test is about the poll's lifetime, not about extraction.
 *
 * On the negative assertion: proving something does NOT happen needs a bounded
 * window, and here the window is the thing being asserted, not a workaround —
 * without the fix the very next tick fires ~2 s after close, well inside it.
 */

const PDF = {
	name: 'poll-orphan.pdf',
	mimeType: 'application/pdf',
	buffer: Buffer.from('%PDF-1.4 e2e extraction poll orphan')
};

async function createInvoiceWithFile(page: Page, invoiceNumber: string): Promise<string> {
	const headers = await authedTenantHeaders(page);
	const created = await page.request.post(`${API_BASE}/api/invoices`, {
		headers,
		data: {
			vendor: 'E2E Poll Orphan Vendor',
			invoice_number: invoiceNumber,
			amount: '77.00',
			currency: 'USD'
		}
	});
	expect(created.ok()).toBeTruthy();
	const id = ((await created.json()) as { id: string }).id;

	// The Extract control only renders when the invoice has a source file.
	const attached = await page.request.post(`${API_BASE}/api/invoices/${id}/file`, {
		headers,
		multipart: { file: PDF }
	});
	expect(attached.ok()).toBeTruthy();
	return id;
}

test.describe('/invoices extraction poll does not outlive the modal', () => {
	let invoiceId: string | null = null;

	test.afterEach(() => {
		if (!invoiceId) return;
		tenantPsql(
			`DELETE FROM workflow_steps WHERE instance_id IN (SELECT id FROM workflow_instances WHERE invoice_id='${invoiceId}')`
		);
		tenantPsql(`DELETE FROM workflow_instances WHERE invoice_id='${invoiceId}'`);
		tenantPsql(`DELETE FROM exceptions WHERE invoice_id='${invoiceId}'`);
		deleteInvoicesWhere(`id='${invoiceId}'`);
		invoiceId = null;
	});

	test('closing mid-extraction stops the poll and leaves the list filtered', async ({ page }) => {
		const number = `E2E-POLLSTOP-${Date.now()}`;
		invoiceId = await createInvoiceWithFile(page, number);
		const detailPath = `/api/invoices/${invoiceId}`;

		// Hold the invoice at `pending` forever so the poll never completes on
		// its own — the modal must be what stops it.
		let pollCount = 0;
		await page.route(
			(url) => url.pathname === detailPath,
			async (route) => {
				if (route.request().method() !== 'GET') return route.continue();
				pollCount++;
				const upstream = await route.fetch();
				const body = (await upstream.json()) as Record<string, unknown>;
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify({ ...body, status: 'pending' })
				});
			}
		);
		await page.route(
			(url) => url.pathname === `${detailPath}/extract`,
			(route) =>
				route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify({ id: invoiceId, status: 'pending' })
				})
		);

		await page.goto('/invoices');
		await expect(page.locator('table tbody tr').first()).toBeVisible();

		// Filter the list down to this one invoice. The filter is the thing the
		// orphaned refresh used to throw away.
		const filtered = page.waitForResponse(
			(r) =>
				r.url().includes('/api/invoices?') &&
				r.url().includes(`search=${encodeURIComponent(number)}`) &&
				r.request().method() === 'GET'
		);
		await page.getByPlaceholder('Search invoices...').fill(number);
		await filtered;
		await expect(page.locator('table tbody tr')).toHaveCount(1);

		// Open it and start the extraction.
		await page.locator('table tbody tr', { hasText: number }).first().click();
		const extract = page.getByRole('button', { name: 'Extract' });
		await expect(extract).toBeVisible();
		await extract.click();

		// Positive signal that the poll loop is genuinely running before we close.
		await expect.poll(() => pollCount, { timeout: 15_000 }).toBeGreaterThan(0);

		// Arm the negative wait BEFORE closing so nothing is missed in between:
		// after teardown neither a further detail poll nor an unfiltered list
		// GET may happen. (The host's own close refetch carries `search=`, so it
		// doesn't match.)
		const strayRequest = page
			.waitForRequest(
				(r) => {
					if (r.method() !== 'GET') return false;
					const url = new URL(r.url());
					if (url.pathname === detailPath) return true;
					return url.pathname === '/api/invoices' && !url.searchParams.get('search');
				},
				{ timeout: 7_000 }
			)
			.then(() => true)
			.catch(() => false);

		await page.getByRole('button', { name: 'Close' }).click();

		expect(
			await strayRequest,
			'the extraction poll kept running (or refreshed the list unfiltered) after the modal closed'
		).toBe(false);

		// The user-visible consequence: the filter the chips/search still claim
		// is the filter the list is actually showing.
		await expect(page.locator('table tbody tr')).toHaveCount(1);
	});
});
