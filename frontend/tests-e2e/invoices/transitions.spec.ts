import { API_BASE, authedTenantHeaders, expect, signInAndWait, test } from '../fixtures/helpers';

async function fetchInvoiceByStatus(
	page: import('@playwright/test').Page,
	wanted: string
) {
	const resp = await page.request.get(
		`${API_BASE}/api/invoices?status=${wanted}`,
		{ headers: await authedTenantHeaders(page) }
	);
	const body = (await resp.json()) as {
		items: Array<{ id: string; invoice_number: string; status: string }>;
	};
	return body.items.find((i) => i.status === wanted) ?? null;
}

async function patchInvoiceStatus(
	page: import('@playwright/test').Page,
	id: string,
	status: string
) {
	return page.request.patch(`${API_BASE}/api/invoices/${id}`, {
		headers: await authedTenantHeaders(page),
		data: { status }
	});
}

/**
 * Ensure the worker's seeded tenant has at least one ready_for_review
 * invoice before running tests in this file.
 *
 * Without this guard, a previous run's Reject/Approve flows have
 * already drained the queue and the first test that calls
 * fetchInvoiceByStatus('ready_for_review') gets null, then `expect(...
 * .toBeTruthy())` fires unhelpfully. We promote a non-immutable
 * invoice via PATCH (which doesn't enforce VALID_TRANSITIONS, so any
 * mutable row will work). The invoice's old status is sacrificed —
 * acceptable for a dev/CI seed that gets reset between sessions.
 */
async function ensureReadyForReviewQueueHasOne(page: import('@playwright/test').Page) {
	const existing = await fetchInvoiceByStatus(page, 'ready_for_review');
	if (existing) return;

	// Walk one page at the endpoint's max page_size (100) — that's
	// plenty for the seed; if a real fixture ever needed more we'd add
	// pagination here. The 'approved' bucket alone has multiple seed
	// rows, so the first page virtually always contains a candidate.
	const list = await page.request.get(`${API_BASE}/api/invoices?page_size=100`, {
		headers: await authedTenantHeaders(page)
	});
	if (list.status() !== 200) {
		throw new Error(`List invoices failed (${list.status()}); cannot prep ready_for_review`);
	}
	const body = (await list.json()) as {
		items?: Array<{ id: string; status: string }>;
	};
	const items = body.items ?? [];
	// Prefer `approved` since the rest of the suite assumes at least
	// one approved invoice exists too — if we steal a rejected one
	// instead we have a fallback supply. `new`/`pending`/`failed` are
	// the last resort because promoting them skips the natural flow.
	const promotable =
		items.find((i) => i.status === 'approved') ||
		items.find((i) => i.status === 'rejected') ||
		items.find((i) => ['new', 'pending', 'failed'].includes(i.status));
	if (!promotable) {
		throw new Error(
			'Cannot find a mutable invoice to promote into ready_for_review — seed exhausted?'
		);
	}
	const resp = await patchInvoiceStatus(page, promotable.id, 'ready_for_review');
	if (resp.status() !== 200) {
		throw new Error(`Failed to promote invoice into ready_for_review: ${resp.status()}`);
	}
}

/**
 * Invoice state-machine transitions exposed in the modal.
 *
 * Seed creates at least one invoice in `ready_for_review` status, which
 * is the only state where the Approve / Reject review-section is
 * enabled. Other statuses must NOT show that affordance.
 *
 * The reject-flow test mutates state then reverses it via the
 * VALID_TRANSITIONS rejected → ready_for_review path. The approve
 * path is one-way (approved cannot return to ready_for_review), so we
 * only assert read-only contract: the Approve button is reachable on
 * a ready_for_review invoice.
 */

test.describe('/invoices status transitions', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
		await ensureReadyForReviewQueueHasOne(page);
		await page.goto('/invoices');
		await page.waitForLoadState('networkidle');
	});

	test('ready_for_review invoice modal shows Approve + Reject buttons', async ({
		page
	}) => {
		const target = await fetchInvoiceByStatus(page, 'ready_for_review');
		expect(target).toBeTruthy();

		await page
			.locator('table tbody tr', { hasText: target!.invoice_number })
			.first()
			.getByRole('button', { name: 'Edit' })
			.click();
		const modal = page.locator('div.modal[role="dialog"]');
		await expect(modal).toBeVisible();

		await expect(modal.locator('.review-section .review-title')).toHaveText('Review');
		await expect(modal.getByRole('button', { name: /^Approve$/ })).toBeVisible();
		await expect(modal.getByRole('button', { name: /^Reject$/ })).toBeVisible();
	});

	test('approved invoice modal does NOT show Approve + Reject buttons', async ({
		page
	}) => {
		const target = await fetchInvoiceByStatus(page, 'approved');
		expect(target).toBeTruthy();

		await page
			.locator('table tbody tr', { hasText: target!.invoice_number })
			.first()
			.getByRole('button', { name: 'Edit' })
			.click();
		const modal = page.locator('div.modal[role="dialog"]');
		await expect(modal).toBeVisible();

		// canReview is false for non-ready_for_review statuses, so the
		// review-section block is not rendered.
		await expect(modal.locator('.review-section')).toHaveCount(0);
	});

	test('Reject flow: ready_for_review → rejected via the modal', async ({
		page
	}) => {
		const target = await fetchInvoiceByStatus(page, 'ready_for_review');
		expect(target).toBeTruthy();

		try {
			await page
				.locator('table tbody tr', { hasText: target!.invoice_number })
				.first()
				.getByRole('button', { name: 'Edit' })
				.click();
			const modal = page.locator('div.modal[role="dialog"]');
			await expect(modal).toBeVisible();

			// Reject button reveals the reject-form (textarea + Confirm Reject).
			await modal.getByRole('button', { name: /^Reject$/ }).click();
			await expect(modal.locator('.reject-form')).toBeVisible();
			await modal.locator('textarea.reject-input').fill('e2e auto-test rejection');

			const rejected = page.waitForResponse(
				(r) =>
					r.url().includes(`/api/invoices/${target!.id}/reject`) &&
					r.request().method() === 'POST'
			);
			await modal.getByRole('button', { name: 'Confirm Reject' }).click();
			await rejected;
			await expect(modal).toBeHidden({ timeout: 5_000 });

			// Re-fetch the specific invoice and confirm status flipped.
			// Asserting "first rejected matches our id" was racy: prior runs
			// can leave other rejected invoices in the DB.
			const fresh = await page.request.get(
				`${API_BASE}/api/invoices/${target!.id}`,
				{ headers: await authedTenantHeaders(page) }
			);
			expect(((await fresh.json()) as { status: string }).status).toBe('rejected');
		} finally {
			// rejected → ready_for_review is in VALID_TRANSITIONS.
			await patchInvoiceStatus(page, target!.id, 'ready_for_review');
		}
	});

	test('Reject form requires a non-empty reason: button stays disabled', async ({
		page
	}) => {
		const target = await fetchInvoiceByStatus(page, 'ready_for_review');
		expect(target).toBeTruthy();

		await page
			.locator('table tbody tr', { hasText: target!.invoice_number })
			.first()
			.getByRole('button', { name: 'Edit' })
			.click();
		const modal = page.locator('div.modal[role="dialog"]');
		await modal.getByRole('button', { name: /^Reject$/ }).click();
		await expect(modal.locator('.reject-form')).toBeVisible();

		const confirm = modal.getByRole('button', { name: 'Confirm Reject' });
		await expect(confirm).toBeDisabled();
		await modal.locator('textarea.reject-input').fill('   '); // whitespace only
		await expect(confirm).toBeDisabled();
		await modal.locator('textarea.reject-input').fill('Real reason');
		await expect(confirm).toBeEnabled();

		// Cancel out — don't actually reject.
		await modal.getByRole('button', { name: /^Cancel$/ }).first().click();
	});
});
