import { expect, test } from '../fixtures/helpers';
import { signInAndWait, authedTenantHeaders, API_BASE } from '../fixtures/helpers';

/**
 * /vendors — "Merge into canonical" consolidation UI.
 *
 * Drives the full flow against the real backend:
 *   1. Seed two near-duplicate ACTIVE vendors sharing a tax id (the
 *      consolidation clusterer groups on exact tax id, so the pair is a
 *      guaranteed deterministic cluster — independent of seed data).
 *   2. Open the "Merge duplicates" modal from the vendors header action.
 *   3. Confirm the cluster renders a canonical-vs-duplicate diff.
 *   4. Merge → the duplicate is soft-retired (status=inactive) and the
 *      vendor list refreshes.
 *
 * The merge endpoint is gated on the granular `vendor.manage` permission. The
 * admin holds it (header action + per-cluster Merge present); a clerk does not
 * (the action is absent).
 */

// A unique tax id per run so the cluster is exactly our two vendors (no
// collision with other workers / prior runs in the shared tenant).
function uniqueTaxId(): string {
	// 9 digits, formatted like an EIN. `Date.now()` keeps it unique per run.
	const n = (Date.now() % 1_000_000_000).toString().padStart(9, '0');
	return `${n.slice(0, 2)}-${n.slice(2)}`;
}

async function createVendor(
	page: import('@playwright/test').Page,
	body: Record<string, unknown>
): Promise<{ id: string; name: string }> {
	const headers = await authedTenantHeaders(page);
	const res = await page.request.post(`${API_BASE}/api/vendors`, {
		headers: { ...headers, 'Content-Type': 'application/json' },
		data: body
	});
	expect(res.ok(), `create vendor ${body.name}: ${res.status()}`).toBeTruthy();
	const v = await res.json();
	return { id: v.id, name: v.name };
}

test.describe('/vendors consolidation merge (admin)', () => {
	// Pre-record the cookie-consent choice so the GDPR banner (which overlays the
	// bottom of the viewport) never intercepts the modal's Close button. The
	// banner is orthogonal to this feature.
	test.beforeEach(async ({ page }) => {
		await page.addInitScript(() => {
			try {
				localStorage.setItem('ap_consent_choice', 'accepted');
			} catch {
				/* about:blank — ignore */
			}
		});
	});

	test('seed a duplicate pair, merge into canonical, list refreshes', async ({ page }) => {
		// A run-unique name token so this run's cluster is exactly our two
		// vendors — robust against vendors any prior run left behind (the tenant
		// is shared across runs; the unique token keeps clusters from bleeding
		// together by name similarity).
		const token = `Qznx${Date.now().toString(36)}`;
		const taxId = uniqueTaxId();
		const canonName = `${token} Holdings Co`;
		const dupeName = `${token} Holdings Company`;

		// Two active vendors sharing a tax id → a deterministic cluster of just
		// these two (the names also pass the fuzzy threshold).
		const a = await createVendor(page, {
			name: canonName,
			code: `${token}-C`,
			tax_id: taxId
		});
		const b = await createVendor(page, {
			name: dupeName,
			tax_id: taxId
		});

		await page.goto('/vendors');
		await page.waitForLoadState('networkidle');

		// Open the consolidation modal from the header action.
		await page.getByRole('button', { name: 'Merge duplicates' }).click();
		const modal = page.getByRole('dialog', { name: 'Vendor consolidation' });
		await expect(modal).toBeVisible();

		// Our pair's cluster renders both names + a Canonical / Duplicate split.
		const cluster = modal.locator('li.cluster', { hasText: token });
		await expect(cluster).toBeVisible();
		await expect(cluster.getByText(canonName, { exact: true })).toBeVisible();
		await expect(cluster.getByText(dupeName, { exact: true })).toBeVisible();
		await expect(cluster.getByText('Canonical', { exact: true })).toBeVisible();
		await expect(cluster.getByText('Duplicate', { exact: true })).toBeVisible();

		// Read which member the backend picked as canonical (most invoices, tie →
		// oldest) so the assertion doesn't assume which of our two it is.
		const canonRow = cluster.locator('tr.canonical-row');
		const canonRowName = (await canonRow.locator('td.m-name').textContent())?.trim();
		const ids: Record<string, string> = { [canonName]: a.id, [dupeName]: b.id };
		const canonId = ids[canonRowName ?? ''];
		const dupeId = canonId === a.id ? b.id : a.id;

		// Arm + confirm the merge (two-step, irreversible-ish). The Merge button
		// carries a per-cluster aria-label (`Merge cluster into <canonical>`).
		await cluster.getByRole('button', { name: `Merge cluster into ${canonRowName}` }).click();
		const confirmBtn = cluster.getByRole('button', { name: 'Confirm merge' });
		await expect(confirmBtn).toBeVisible();
		await confirmBtn.click();

		// The merged cluster drops out of the modal list.
		await expect(modal.locator('li.cluster', { hasText: token })).toHaveCount(0);

		await modal.getByRole('button', { name: 'Close' }).click();
		await expect(modal).toBeHidden();

		// Backend truth: the duplicate is soft-retired, the canonical stays active.
		const headers = await authedTenantHeaders(page);
		const check = await page.request.get(`${API_BASE}/api/vendors/${dupeId}`, { headers });
		expect(check.ok()).toBeTruthy();
		expect((await check.json()).status).toBe('inactive');

		const checkCanon = await page.request.get(`${API_BASE}/api/vendors/${canonId}`, { headers });
		expect((await checkCanon.json()).status).toBe('active');
	});
});

test.describe('/vendors consolidation (clerk has no access)', () => {
	test.use({ storageState: { cookies: [], origins: [] } });

	test('ap_clerk does not see the Merge duplicates action', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);
		await page.goto('/vendors');
		await page.waitForLoadState('networkidle');

		await expect(page.getByRole('button', { name: 'Merge duplicates' })).toHaveCount(0);
	});
});
