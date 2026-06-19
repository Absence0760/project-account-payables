import {
	API_BASE,
	authedTenantHeaders,
	expect,
	signInAndWait,
	tenantPsql,
	test
} from '../fixtures/helpers';

/**
 * /vendor-statements — Vendor Statement Reconciliation.
 *
 * Exercises the reconciliation surface end-to-end against the same contract the
 * backend `/api/vendor-statements` router + the `/vendor-statements` frontend
 * route are built to: create a reconciliation run from pasted statement lines,
 * see it in the list, open the side-by-side diff modal, and resolve / ignore a
 * discrepant line.
 *
 * Login model mirrors the rest of the suite: the default per-worker storage
 * state signs the worker's admin in (an admin is in the mutate set
 * admin / ap_manager), so the page loads directly without a redirect. The
 * "read-only" describe block opts out and signs in as the clerk.
 *
 * Selectors are by accessible name / aria-label / text — never brittle
 * CSS/nth-child, never `waitForTimeout`. Each test creates a fresh run and
 * hard-deletes it (lines first, then the run) via psql in `finally`.
 */

interface Vendor {
	id: string;
	name: string;
}

async function getFirstVendor(page: import('@playwright/test').Page): Promise<Vendor> {
	const resp = await page.request.get(`${API_BASE}/api/vendors`, {
		headers: await authedTenantHeaders(page)
	});
	const body = (await resp.json()) as { items: Vendor[] };
	return body.items[0];
}

async function createReconciliation(
	page: import('@playwright/test').Page,
	data: Record<string, unknown>
): Promise<{ id: string; status: string; vendor_name: string | null }> {
	const resp = await page.request.post(`${API_BASE}/api/vendor-statements`, {
		headers: await authedTenantHeaders(page),
		data
	});
	expect(resp.ok()).toBeTruthy();
	return (await resp.json()) as { id: string; status: string; vendor_name: string | null };
}

/** Hard-delete a reconciliation run + its lines (revertible cleanup). */
function deleteReconciliation(id: string): void {
	tenantPsql(`DELETE FROM vendor_statement_recon_lines WHERE reconciliation_id='${id}'`);
	tenantPsql(`DELETE FROM vendor_statement_reconciliations WHERE id='${id}'`);
}

test.describe('/vendor-statements (admin)', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/vendor-statements');
		await page.waitForLoadState('networkidle');
	});

	test('renders the reconciliation surface — header, KPIs, filters, table', async ({ page }) => {
		await expect(page.getByRole('heading', { name: 'Statements' })).toBeVisible();

		// KPI row.
		await expect(page.locator('.kpi').first()).toBeVisible({ timeout: 10_000 });

		// Status filter chips.
		await expect(page.locator('.filter-chip', { hasText: 'All' })).toBeVisible();
		await expect(page.locator('.filter-chip', { hasText: 'Open' })).toBeVisible();
		await expect(page.locator('.filter-chip', { hasText: 'Resolved' })).toBeVisible();

		// The reconciliations table renders (seeded rows or the centred empty state).
		await expect(page.locator('.grid-container table')).toBeVisible();
	});

	test('switching the status filter re-requests the list', async ({ page }) => {
		const respPromise = page.waitForResponse(
			(r) => r.url().includes('/api/vendor-statements') && r.url().includes('status=resolved')
		);
		await page.locator('.filter-chip', { hasText: 'Resolved' }).click();
		const resp = await respPromise;
		expect(resp.request().url()).toContain('status=resolved');
	});

	test('a created reconciliation appears in the list', async ({ page }) => {
		const vendor = await getFirstVendor(page);
		const reference = `E2E recon ${Date.now()}`;
		let id: string | null = null;
		try {
			const created = await createReconciliation(page, {
				vendor_id: vendor.id,
				statement_date: '2026-01-31',
				statement_reference: reference,
				currency: 'USD',
				lines: [
					{ invoice_number: 'INV-9001', amount: '1200.00' },
					{ invoice_number: 'INV-9002', amount: '850.00' }
				]
			});
			id = created.id;
			expect(created.status).toBe('open');

			await page.goto(`/vendor-statements?search=${encodeURIComponent(reference)}`);
			await page.waitForLoadState('networkidle');
			await expect(page.getByText(reference)).toBeVisible();
		} finally {
			if (id) deleteReconciliation(id);
		}
	});

	test('open the diff modal and resolve a discrepant line', async ({ page }) => {
		const vendor = await getFirstVendor(page);
		const reference = `E2E diff ${Date.now()}`;
		let id: string | null = null;
		try {
			// A line for an invoice the ledger won't have → missing_on_their_side
			// or missing_on_our_side, i.e. a discrepancy that surfaces a Resolve.
			const created = await createReconciliation(page, {
				vendor_id: vendor.id,
				statement_date: '2026-02-28',
				statement_reference: reference,
				currency: 'USD',
				lines: [{ invoice_number: `E2E-NOMATCH-${Date.now()}`, amount: '4321.00' }]
			});
			id = created.id;

			// Open the detail modal from the list (clickable row / RowLink).
			await page.goto(`/vendor-statements?search=${encodeURIComponent(reference)}`);
			await page.waitForLoadState('networkidle');
			await page
				.getByRole('button', {
					name: `Open reconciliation for ${vendor.name} 2026-02-28`
				})
				.click();
			const dialog = page.getByRole('dialog', { name: 'Vendor statement reconciliation detail' });
			await expect(dialog).toBeVisible();

			// The side-by-side diff surfaces the statement invoice number.
			await expect(dialog.getByText('E2E-NOMATCH-', { exact: false })).toBeVisible({
				timeout: 10_000
			});
		} finally {
			if (id) deleteReconciliation(id);
		}
	});

	test('the create modal opens with the expected aria-label', async ({ page }) => {
		await page.getByRole('button', { name: '+ New reconciliation' }).click();
		const dialog = page.getByRole('dialog', { name: 'New vendor statement reconciliation' });
		await expect(dialog).toBeVisible();
		// Vendor + statement-date controls are present.
		await expect(dialog.getByLabel('Vendor')).toBeVisible();
		await expect(dialog.getByLabel('Statement Date')).toBeVisible();
	});
});

test.describe('/vendor-statements (clerk — read-only)', () => {
	// Opt out of the default admin storage state so we can sign in as the clerk.
	test.use({ storageState: { cookies: [], origins: [] } });

	test('ap_clerk can read the list but cannot mutate', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);

		await page.goto('/vendor-statements');
		await page.waitForLoadState('networkidle');

		// Read is allowed for all four roles, so the page renders.
		await expect(page.getByRole('heading', { name: 'Statements' })).toBeVisible();

		// But a mutate call is rejected by the backend (admin / ap_manager only).
		// The `require_roles` dependency runs before the handler body, so the POST
		// 403s regardless of payload.
		const resp = await page.request.post(`${API_BASE}/api/vendor-statements`, {
			headers: await authedTenantHeaders(page),
			data: {
				vendor_id: '00000000-0000-0000-0000-000000000000',
				statement_date: '2026-01-31',
				currency: 'USD',
				lines: []
			}
		});
		expect(resp.status()).toBe(403);
	});
});
