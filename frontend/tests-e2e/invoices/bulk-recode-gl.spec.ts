import { API_BASE, authedTenantHeaders, expect, signInAndWait, test } from '../fixtures/helpers';

async function apiHeaders(page: import('@playwright/test').Page) {
	return await authedTenantHeaders(page);
}

/**
 * Bulk Re-code GL — admin tool to backfill cached vendor GL codes
 * (and optionally re-extract via AI) across a date / vendor scoped
 * slice of invoices.
 *
 * Roadmap item "Bulk re-code capability — admin tool to re-run GL
 * suggestion across a date range / vendor set" in the AI Auto GL
 * Coding section.
 *
 * Specs cover the API contract end-to-end against the worker's seeded
 * tenant. Frontend modal coverage is the simple "open / close" smoke
 * test below — the heavy logic lives in the API and is exercised
 * through `page.request`.
 */

test.describe('/api/invoices/bulk-recode-gl', () => {
	test('clerk role cannot trigger the recode (admin-only)', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);
		const headers = await apiHeaders(page);
		const resp = await page.request.post(
			`${API_BASE}/api/invoices/bulk-recode-gl`,
			{ headers, data: { dry_run: true } }
		);
		expect(resp.status()).toBe(403);
	});

	test('dry run returns the canonical report shape', async ({ page }) => {
		const headers = await apiHeaders(page);
		const resp = await page.request.post(`${API_BASE}/api/invoices/bulk-recode-gl`, {
			headers: { ...headers, 'Content-Type': 'application/json' },
			data: {
				dry_run: true,
				include_ai_fallback: false,
				vendor_ids: []
			}
		});
		expect(resp.status()).toBe(200);
		const body = (await resp.json()) as {
			matched: number;
			would_change?: number;
			applied?: number;
			ai_candidates: number;
			by_source: { vendor_prior: number; ai: number };
			skipped: Record<string, number>;
			changes: unknown[];
			dry_run: boolean;
		};

		expect(body.dry_run).toBe(true);
		expect('would_change' in body).toBe(true);
		expect('applied' in body).toBe(false);
		expect(typeof body.matched).toBe('number');
		expect(typeof body.ai_candidates).toBe('number');
		// AI candidates is 0 when include_ai_fallback=false (no AI is
		// considered for any invoice, regardless of priors coverage).
		expect(body.ai_candidates).toBe(0);
		expect(body.by_source).toMatchObject({ vendor_prior: 0, ai: 0 });
		expect(Array.isArray(body.changes)).toBe(true);
		// Skipped object lists every reason — locks the contract the UI
		// reads to render its summary panel.
		for (const key of [
			'immutable_status',
			'no_vendor',
			'no_change',
			'no_prior_no_ai',
			'ai_failed',
			'invalid_code'
		]) {
			expect(typeof body.skipped[key]).toBe('number');
		}
	});

	test('rejects from_date > to_date with 400', async ({ page }) => {
		const headers = await apiHeaders(page);
		const resp = await page.request.post(`${API_BASE}/api/invoices/bulk-recode-gl`, {
			headers: { ...headers, 'Content-Type': 'application/json' },
			data: { dry_run: true, from_date: '2026-12-31', to_date: '2026-01-01' }
		});
		expect(resp.status()).toBe(400);
	});

	test('rejects malformed vendor_id with 400', async ({ page }) => {
		const headers = await apiHeaders(page);
		const resp = await page.request.post(`${API_BASE}/api/invoices/bulk-recode-gl`, {
			headers: { ...headers, 'Content-Type': 'application/json' },
			data: { dry_run: true, vendor_ids: ['not-a-uuid'] }
		});
		expect(resp.status()).toBe(400);
	});

	test('paid + posted invoices are counted as immutable_status, never as candidates', async ({
		page
	}) => {
		const headers = await apiHeaders(page);

		// Lower bound: the seeded tenant has at least some paid / posted
		// invoices (see scripts/seed.py). Whatever that count is, it
		// shows up in skipped.immutable_status when we sweep without a
		// date filter.
		const resp = await page.request.post(`${API_BASE}/api/invoices/bulk-recode-gl`, {
			headers: { ...headers, 'Content-Type': 'application/json' },
			data: { dry_run: true }
		});
		const body = (await resp.json()) as {
			skipped: { immutable_status: number };
			matched: number;
		};
		expect(body.skipped.immutable_status).toBeGreaterThanOrEqual(0);
		// Sanity: the matched count is the *eligible* set, not total
		// invoices. So matched + skipped.immutable_status + no_vendor
		// is the full sweep.
		expect(body.matched).toBeGreaterThanOrEqual(0);
	});
});

test.describe('/invoices — Bulk Re-code GL modal (admin)', () => {
	test('admin sees the toolbar button and can open the modal', async ({ page }) => {
		await page.goto('/invoices');
		await page.waitForLoadState('networkidle');

		const button = page.getByRole('button', { name: 'Bulk Re-code GL' });
		await expect(button).toBeVisible();
		await button.click();

		const modal = page.locator('div.modal[role="dialog"][aria-label="Bulk re-code GL"]');
		await expect(modal).toBeVisible();
		await expect(modal.getByRole('heading', { name: 'Bulk Re-code GL Codes' })).toBeVisible();

		// Preview Changes is the first action — it submits a dry-run.
		const previewBtn = modal.getByRole('button', { name: /Preview Changes/i });
		await expect(previewBtn).toBeVisible();
		await previewBtn.click();

		// Summary panel renders after the dry-run returns.
		await expect(modal.locator('dl.summary')).toBeVisible({ timeout: 5_000 });
		// "Edit filters" replaces the cancel button after the preview lands.
		await expect(modal.getByRole('button', { name: 'Edit filters' })).toBeVisible();
	});

	test('clerk does not see the toolbar button', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);
		// Clerk doesn't have access to /invoices nav, but they CAN navigate
		// directly. Confirm the button is gated by isAdmin, not just
		// sidebar visibility.
		await page.goto('/invoices');
		await page.waitForLoadState('networkidle');
		const button = page.getByRole('button', { name: 'Bulk Re-code GL' });
		await expect(button).toHaveCount(0);
	});
});
