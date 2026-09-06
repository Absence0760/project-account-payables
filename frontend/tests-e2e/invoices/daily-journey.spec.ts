import {
	API_BASE,
	authedTenantHeaders,
	expect,
	signInAndWait,
	tenantPsql,
	test
} from '../fixtures/helpers';

/**
 * /invoices — the AP user's daily approval journey, end to end through
 * the real UI:
 *
 *   land on the invoice queue → filter to the review queue (the
 *   "Ready for Review" chip) → open a ready_for_review invoice from the
 *   row → click Approve in the modal → confirm the modal closes, the
 *   invoice flips to `approved` (status + audit row), and it has left
 *   the review queue.
 *
 * This is the single most-walked path in the app and the genuine gap in
 * the existing suite: `transitions.spec.ts` asserts the Approve button
 * is *visible* on a ready_for_review invoice but never clicks it, and
 * never confirms the post-approve state or that the row leaves the
 * queue. The reject leg is fully covered there, so this file owns the
 * approve leg of the journey.
 *
 * The approve transition is one-way (approved cannot return to
 * ready_for_review), so each test promotes a fresh row into
 * ready_for_review via PATCH before driving the UI — the promoted row's
 * old status is sacrificed, which is fine for a dev/CI seed that resets
 * between sessions (same model `transitions.spec.ts` uses).
 */

type Inv = { id: string; invoice_number: string; status: string };

async function listInvoices(
	page: import('@playwright/test').Page,
	query = ''
): Promise<Inv[]> {
	const resp = await page.request.get(`${API_BASE}/api/invoices?page_size=100${query}`, {
		headers: await authedTenantHeaders(page)
	});
	if (resp.status() !== 200) {
		throw new Error(`List invoices failed (${resp.status()})`);
	}
	return ((await resp.json()) as { items: Inv[] }).items;
}

/**
 * Force an invoice to a given status via direct SQL.
 * PATCH /api/invoices intentionally ignores the `status` field (it was removed
 * from InvoiceUpdate to prevent status-injection), so for test setup / teardown
 * that needs to place an invoice in a specific status regardless of the current
 * state, direct SQL is the only reliable path. This is the same pattern used in
 * transitions.spec.ts and void-cancel.spec.ts.
 */
function forceStatus(id: string, status: string): void {
	tenantPsql(`UPDATE invoices SET status='${status}' WHERE id='${id}'`);
}

async function getInvoice(
	page: import('@playwright/test').Page,
	id: string
): Promise<Inv> {
	const resp = await page.request.get(`${API_BASE}/api/invoices/${id}`, {
		headers: await authedTenantHeaders(page)
	});
	return (await resp.json()) as Inv;
}

/**
 * Candidate invoice ids in one of `statuses` that THIS admin can legitimately
 * approve.
 *
 * Two filters, both load-bearing:
 *
 *  - `uploaded_by_id IS NULL` — segregation of duties refuses an approval by
 *    the invoice's own uploader (403). Sibling specs create invoices through
 *    the UI as the same worker admin (`invoices/create-manual`,
 *    `invoices/file-management`) and do not delete them, so the tenant
 *    accumulates rows this admin uploaded. Picking one made the approve 403,
 *    and — because the pick had already been promoted — left it sitting in
 *    `ready_for_review` where the "prefer an existing one" branch re-selected
 *    it on every later run. One poisoned row, permanently red. Seed rows carry
 *    no uploader, which is exactly the set that is approvable here.
 *  - a non-empty `invoice_number` — the spec locates the row by its number via
 *    the RowLink, and a blank number makes the selectors ambiguous.
 *
 * The uploader is only visible in SQL (`InvoiceResponse` deliberately doesn't
 * carry it), so the candidate set is resolved there rather than from the list
 * endpoint.
 */
function approvableIds(statuses: string[]): string[] {
	const list = statuses.map((s) => `'${s}'`).join(',');
	return tenantPsql(
		`SELECT id FROM invoices
		  WHERE status IN (${list})
		    AND uploaded_by_id IS NULL
		    AND COALESCE(invoice_number, '') <> ''
		  ORDER BY created_at, id`
	)
		.trim()
		.split('\n')
		.map((l) => l.trim())
		.filter(Boolean);
}

/**
 * Promote a non-immutable invoice into ready_for_review and return it.
 * Prefers an existing ready_for_review row; otherwise promotes a
 * mutable one (PATCH doesn't enforce VALID_TRANSITIONS). Mirrors the
 * helper in transitions.spec.ts so the two files prep identically.
 */
async function ensureReadyForReview(
	page: import('@playwright/test').Page
): Promise<Inv> {
	const existing = approvableIds(['ready_for_review']);
	if (existing.length > 0) return await getInvoice(page, existing[0]);

	// Same preference order as before: sacrifice a rejected row first, then an
	// un-reviewed one, and only fall back to an approved row.
	const promotable =
		approvableIds(['rejected'])[0] ??
		approvableIds(['new', 'pending', 'failed'])[0] ??
		approvableIds(['approved'])[0];
	if (!promotable) {
		throw new Error('No mutable invoice to promote into ready_for_review — seed exhausted?');
	}
	forceStatus(promotable, 'ready_for_review');
	return { ...(await getInvoice(page, promotable)), status: 'ready_for_review' };
}

/**
 * Navigate to /invoices and land authenticated. The storage-state JWT is
 * in localStorage, but on a worker's very first navigation the SPA's auth
 * store can read storage before it's applied and bounce to /login. When
 * that happens the token is still valid — a reload re-inits the store with
 * it present. We wait on the real authed signal (the search box), not a
 * timeout. Mirrors the dashboard-URL wait in fixtures `_ensureAdminStorageState`.
 */
/**
 * Sign in (admin) and land on /invoices. We sign in explicitly rather than
 * lean on the worker's storage-state default: the auth store snapshots
 * `loggedIn = hasToken()` at module-eval time, so the very first navigation
 * in a worker can race storage-state application and bounce to /login. An
 * explicit signInAndWait is deterministic (it's the pattern the auth +
 * cfo-approval specs use), then we navigate to the queue.
 */
async function signInAndGotoInvoices(
	page: import('@playwright/test').Page,
	creds: { email: string; password: string }
) {
	await signInAndWait(page, creds);
	await page.goto('/invoices');
	await expect(page.getByPlaceholder('Search invoices...')).toBeVisible();
}

// These tests drive the login UI explicitly, so opt out of the worker's
// pre-signed-in storage-state default (per fixtures/helpers.ts).
test.use({ storageState: { cookies: [], origins: [] } });

test.describe('/invoices daily approval journey', () => {
	test.beforeEach(() => {
		// Ensure the tenant's "Default Workflow" (with approval enabled) is the
		// active default. `workflows/` test runs can create extra workflow
		// definitions with is_default=TRUE, including ones with all steps disabled.
		// If a stale is_default=TRUE definition is picked instead of "Default
		// Workflow", the "Ready for Review" chip is hidden because approval=false.
		// We demote all non-seed definitions and guarantee "Default Workflow" is
		// active and default. The multi-entity entity_id context is irrelevant here
		// because the app-level fallback (fixed in workflow_engine.py) now correctly
		// finds any active workflow when no entity_id=NULL org-wide definition
		// exists — the entity-scoped "Default Workflow" is always in scope.
		tenantPsql(
			`UPDATE workflow_definitions SET is_default=FALSE, is_active=FALSE WHERE name <> 'Default Workflow'`
		);
		tenantPsql(
			`UPDATE workflow_definitions SET is_default=TRUE, is_active=TRUE WHERE name='Default Workflow'`
		);
	});

	test('queue → Ready-for-Review filter → open → Approve → leaves the queue', async ({
		page,
		tenantAdmin
	}) => {
		// 1. Sign in and land on the invoice queue (the AP user's home base).
		await signInAndGotoInvoices(page, tenantAdmin);

		const target = await ensureReadyForReview(page);
		// Reflect the freshly-promoted row in the table.
		await page.reload();
		await page.waitForLoadState('networkidle');

		// 2. Filter to the review queue via the "Ready for Review" chip.
		//    Clicking it fires a filtered fetch (status=ready_for_review);
		//    wait on that real network signal, not a timeout.
		const reviewChip = page.locator('.filter-chip', { hasText: /^Ready for Review\s/ });
		await expect(reviewChip).toBeVisible();
		const filtered = page.waitForResponse(
			(r) =>
				r.url().includes('/api/invoices') &&
				r.url().includes('status=ready_for_review') &&
				r.request().method() === 'GET'
		);
		await reviewChip.click();
		await filtered;
		await expect(reviewChip).toHaveClass(/active/);

		// Our target row is present and its badge reads "Ready for Review".
		const row = page
			.locator('table tbody tr', { hasText: target.invoice_number })
			.first();
		await expect(row).toBeVisible();
		await expect(row.locator('.badge')).toHaveText('Ready for Review');

		// 3. Open the invoice from the row (RowLink → modal).
		await row.getByRole('button', { name: `Edit invoice ${target.invoice_number}` }).click();
		const modal = page.locator('div.modal[role="dialog"]');
		await expect(modal).toBeVisible();
		await expect(modal.locator('.review-section .review-title')).toHaveText('Review');

		// 4. Approve. Wait on the POST /approve response — the real signal.
		const approved = page.waitForResponse(
			(r) =>
				r.url().includes(`/api/invoices/${target.id}/approve`) &&
				r.request().method() === 'POST' &&
				r.status() === 200
		);
		// On close, the page re-applies its active filter (the
		// Ready-for-Review chip), refetching status=ready_for_review. That
		// filtered list is the authoritative post-approve view — wait on it
		// so the row-count assertion isn't racing the modal's own
		// (unfiltered) refresh.
		const refiltered = page.waitForResponse(
			(r) =>
				r.url().includes('/api/invoices') &&
				r.url().includes('status=ready_for_review') &&
				r.request().method() === 'GET' &&
				r.status() === 200
		);
		await modal.getByRole('button', { name: /^Approve$/ }).click();
		await approved;

		// 5. The modal closes on success (handleApprove calls onclose()).
		await expect(modal).toBeHidden({ timeout: 5_000 });
		await refiltered;

		// 6. Backend truth: the invoice is now `approved`...
		const fresh = await getInvoice(page, target.id);
		expect(fresh.status).toBe('approved');

		// ...and an append-only audit row was written for the approval.
		// GET /api/audit/invoice/{id} (admin/manager/CFO) returns the
		// chronological trail as a flat list of entries.
		const auditResp = await page.request.get(
			`${API_BASE}/api/audit/invoice/${target.id}`,
			{ headers: await authedTenantHeaders(page) }
		);
		expect(auditResp.status()).toBe(200);
		const audit = (await auditResp.json()) as Array<{ action: string }>;
		expect(audit.some((e) => e.action === 'invoice.approved')).toBe(true);

		// 7. UI truth: still on the Ready-for-Review filter, the approved
		//    invoice has left the queue. Locate by the exact per-row
		//    "Edit invoice {number}" control (RowLink ariaLabel) so the
		//    match is precise — substring hasText could false-match a
		//    similarly-numbered row.
		await expect(
			page.getByRole('button', { name: `Edit invoice ${target.invoice_number}` })
		).toHaveCount(0);
	});

	test('Approve button cancels back without mutating when the modal is dismissed', async ({
		page,
		tenantAdmin
	}) => {
		// Guards the read-only contract: opening the review modal and
		// closing it (Esc) must NOT approve the invoice — only the explicit
		// Approve click transitions it. Keeps the seed row reusable.
		await signInAndGotoInvoices(page, tenantAdmin);

		const target = await ensureReadyForReview(page);
		await page.reload();
		await page.waitForLoadState('networkidle');

		const reviewChip = page.locator('.filter-chip', { hasText: /^Ready for Review\s/ });
		const filtered = page.waitForResponse(
			(r) =>
				r.url().includes('/api/invoices') &&
				r.url().includes('status=ready_for_review') &&
				r.request().method() === 'GET'
		);
		await reviewChip.click();
		await filtered;

		const row = page
			.locator('table tbody tr', { hasText: target.invoice_number })
			.first();
		await row.getByRole('button', { name: `Edit invoice ${target.invoice_number}` }).click();
		const modal = page.locator('div.modal[role="dialog"]');
		await expect(modal).toBeVisible();
		await expect(modal.getByRole('button', { name: /^Approve$/ })).toBeVisible();

		// Dismiss with Esc (Modal owns the keyboard close) — no approve POST.
		await page.keyboard.press('Escape');
		await expect(modal).toBeHidden();

		// Still ready_for_review — the modal lifecycle alone doesn't mutate.
		const fresh = await getInvoice(page, target.id);
		expect(fresh.status).toBe('ready_for_review');
	});
});
