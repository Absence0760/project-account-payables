import { API_BASE, currentTenantSlug, expect, signInAndWait, test } from '../fixtures/helpers';
import { expectNoA11yViolations } from '../a11y/axe-helper';

/**
 * /audit → Approval-signature verification panel.
 *
 * Surfaces the SOX non-repudiation control test that had no frontend caller:
 *  - GET /api/audit/verify-signatures?start=&end=[&limit=]  (population sweep)
 *  - GET /api/audit/invoice/{id}/verify-signatures          (the drill-down)
 *
 * Both are `require_roles(ADMIN, CFO)` and BOTH WRITE AN `audit.viewed` ACCESS
 * ROW, so the panel loads only on an explicit click — the specs below assert
 * that too (no request before the button is pressed).
 *
 * The load-bearing product rule under test is that `unsigned` is presented
 * apart from `invalid`: an approval written before a signing key was
 * configured has nothing to verify and must never read as tampering
 * (`backend/docs/approval-signatures.md`). The mocked responses pin that
 * rendering; the first test proves the real endpoint is wired.
 */

/** A response shaped exactly like `verify_signatures_for_period` returns. */
function report(over: Record<string, unknown> = {}) {
	return {
		start: '2026-01-01',
		end: '2026-03-31',
		signing_configured: true,
		invoices_covered: 12,
		approvals_checked: 14,
		valid: 11,
		invalid: 1,
		unsigned: 2,
		findings: [
			{
				invoice_id: '11111111-1111-1111-1111-111111111111',
				invoice_number: 'INV-TAMPERED',
				audit_row_id: 'aaaaaaaa-0000-0000-0000-000000000001',
				actor_id: '22222222-2222-2222-2222-222222222222',
				actor: 'A. Manager',
				signed_at: '2026-02-14T09:12:03+00:00',
				verdict: 'invalid'
			},
			{
				invoice_id: '33333333-3333-3333-3333-333333333333',
				invoice_number: 'INV-PRE-KEY',
				audit_row_id: 'aaaaaaaa-0000-0000-0000-000000000002',
				actor_id: null,
				actor: null,
				signed_at: null,
				verdict: 'unsigned'
			},
			{
				// The direct-DB tamper the control exists to catch: `details` is
				// not a JSON object at all, so the backend returns ONE `unsigned`
				// finding with nothing resolvable on it rather than a 500 that
				// would lose the rest of the period's evidence.
				invoice_id: '44444444-4444-4444-4444-444444444444',
				invoice_number: null,
				audit_row_id: 'aaaaaaaa-0000-0000-0000-000000000003',
				actor_id: null,
				actor: null,
				signed_at: null,
				verdict: 'unsigned'
			}
		],
		findings_truncated: false,
		...over
	};
}

test.describe('audit signature verification (admin)', () => {
	test('runs a real date-range verification and renders the counts', async ({ page }) => {
		let calls = 0;
		page.on('request', (r) => {
			if (r.url().includes('/api/audit/verify-signatures')) calls += 1;
		});

		await page.goto('/audit');
		await expect(page.getByRole('heading', { name: 'Approval-signature verification' })).toBeVisible();

		// The read is audited, so nothing may be fetched before the click.
		await expect(page.getByTestId('verify-counts')).toHaveCount(0);
		expect(calls).toBe(0);

		const verified = page.waitForResponse(
			(r) => r.url().includes('/api/audit/verify-signatures') && r.request().method() === 'GET'
		);
		await page.getByTestId('run-verification').click();
		const resp = await verified;
		expect(resp.status()).toBe(200);

		const counts = page.getByTestId('verify-counts');
		await expect(counts).toBeVisible();
		// The two claims are presented as their own figures, not one total.
		await expect(counts.getByText('Invalid', { exact: true })).toBeVisible();
		await expect(counts.getByText('Unsigned', { exact: true })).toBeVisible();
		expect(calls).toBe(1);
	});

	test('keeps unsigned visually and textually distinct from invalid', async ({ page }) => {
		await page.goto('/audit');
		await page.route('**/api/audit/verify-signatures*', async (route) => {
			await route.fulfill({ json: report() });
		});
		await page.getByTestId('run-verification').click();

		const counts = page.getByTestId('verify-counts');
		await expect(counts).toBeVisible();

		// Separate cards, separate numbers, separate qualifying sub-lines.
		await expect(counts.getByText('Invalid', { exact: true })).toBeVisible();
		await expect(counts.getByText('Unsigned', { exact: true })).toBeVisible();
		await expect(counts.getByText('Digest no longer re-derives — investigate')).toBeVisible();
		await expect(counts.getByText('Nothing to verify — not a tamper signal')).toBeVisible();

		// Only `invalid` is tinted as an alarm; `unsigned` must not be, or a
		// key-rollout backlog reads as fraud.
		const invalidCard = counts.locator('.kpi', { hasText: 'Digest no longer re-derives' });
		const unsignedCard = counts.locator('.kpi', { hasText: 'Nothing to verify' });
		await expect(invalidCard).toHaveClass(/highlight-red/);
		await expect(unsignedCard).not.toHaveClass(/highlight-red/);

		// The wording spells out that the two are different claims.
		await expect(page.getByText(/counted separately on purpose/)).toBeVisible();

		// Findings carry their verdict on the row and a differently-toned badge.
		const findings = page.getByTestId('verify-finding');
		await expect(findings).toHaveCount(3);
		await expect(page.locator('[data-testid="verify-finding"][data-verdict="invalid"]')).toHaveCount(1);
		await expect(page.locator('[data-testid="verify-finding"][data-verdict="unsigned"]')).toHaveCount(2);
		await expect(page.locator('.badge.danger.verdict-invalid')).toHaveCount(1);
		await expect(page.locator('.badge.muted.verdict-unsigned')).toHaveCount(2);
	});

	test('renders a corrupt row (non-object details) as one unsigned finding', async ({ page }) => {
		await page.goto('/audit');
		await page.route('**/api/audit/verify-signatures*', async (route) => {
			await route.fulfill({ json: report() });
		});
		await page.getByTestId('run-verification').click();

		// The finding with no invoice number, no actor and no signed_at still
		// renders a complete row — it must not blank the table or throw.
		const corrupt = page
			.locator('[data-testid="verify-finding"][data-verdict="unsigned"]')
			.filter({ hasText: '44444444' });
		await expect(corrupt).toHaveCount(1);
		await expect(corrupt.locator('td').nth(1)).toHaveText('—');
		await expect(corrupt.locator('td').nth(2)).toHaveText('—');
	});

	test('explains an unconfigured signing key instead of showing a bare red result', async ({
		page
	}) => {
		await page.goto('/audit');
		await page.route('**/api/audit/verify-signatures*', async (route) => {
			await route.fulfill({
				json: report({
					signing_configured: false,
					valid: 0,
					invalid: 0,
					unsigned: 3,
					findings: report().findings.filter((f) => f.verdict === 'unsigned')
				})
			});
		});
		await page.getByTestId('run-verification').click();

		await expect(page.getByTestId('verify-not-configured')).toBeVisible();
		await expect(page.getByTestId('verify-not-configured')).toContainText(
			'no signing key is configured'
		);
		// Nothing is flagged as tampered, and the invalid card is not alarmed.
		const counts = page.getByTestId('verify-counts');
		const invalidCard = counts.locator('.kpi', { hasText: 'Digest no longer re-derives' });
		await expect(invalidCard).not.toHaveClass(/highlight-red/);
		await expect(page.locator('.badge.danger.verdict-invalid')).toHaveCount(0);
	});

	test('a clean period reports the control test as passed', async ({ page }) => {
		await page.goto('/audit');
		await page.route('**/api/audit/verify-signatures*', async (route) => {
			await route.fulfill({
				json: report({ valid: 14, invalid: 0, unsigned: 0, findings: [] })
			});
		});
		await page.getByTestId('run-verification').click();

		await expect(page.getByTestId('verify-clean')).toBeVisible();
		await expect(page.getByTestId('verify-finding')).toHaveCount(0);
	});

	test('the rendered findings state is axe-clean at WCAG 2.2 AA', async ({ page }) => {
		await page.goto('/audit');
		await page.route('**/api/audit/verify-signatures*', async (route) => {
			await route.fulfill({ json: report() });
		});
		await page.getByTestId('run-verification').click();
		await expect(page.getByTestId('verify-counts')).toBeVisible();
		await expectNoA11yViolations(page);
	});

	test('drills from a finding into that invoice’s approvals', async ({ page }) => {
		await page.goto('/audit');
		await page.route('**/api/audit/verify-signatures*', async (route) => {
			await route.fulfill({ json: report() });
		});
		await page.route('**/api/audit/invoice/*/verify-signatures', async (route) => {
			await route.fulfill({
				json: {
					invoice_id: '11111111-1111-1111-1111-111111111111',
					signing_configured: true,
					approvals: [
						{
							audit_row_id: 'bbbbbbbb-0000-0000-0000-000000000001',
							signed_at: '2026-02-14T09:12:03+00:00',
							actor: 'A. Manager',
							signed: true,
							valid: false
						}
					]
				}
			});
		});
		await page.getByTestId('run-verification').click();

		// No drill-down request until a specific finding is chosen (audited read).
		await expect(page.getByTestId('verify-drill')).toHaveCount(0);
		await page.getByRole('button', { name: 'Approvals on INV-TAMPERED' }).click();

		const drill = page.getByTestId('verify-drill');
		await expect(drill).toBeVisible();
		await expect(drill.getByRole('heading', { name: 'Approvals on INV-TAMPERED' })).toBeVisible();
		await expect(drill.getByTestId('verify-approval')).toHaveCount(1);
		await expect(drill.locator('.badge.danger.verdict-invalid')).toHaveCount(1);
	});
});

test.describe('audit signature verification (clerk RBAC)', () => {
	test.use({ storageState: { cookies: [], origins: [] } });

	test('a clerk cannot reach the panel, and the endpoint 403s them', async ({
		page,
		tenantClerk
	}) => {
		await signInAndWait(page, tenantClerk);
		await page.goto('/audit');

		// The console renders its access-denied panel; the verification controls
		// never exist for a non-admin/CFO caller.
		await expect(page.locator('.audit-denied')).toBeVisible();
		await expect(page.getByTestId('run-verification')).toHaveCount(0);
		await expect(
			page.getByRole('heading', { name: 'Approval-signature verification' })
		).toHaveCount(0);

		// RBAC parity: the backend refuses the same caller directly.
		const token = await page.evaluate(() => localStorage.getItem('auth_token'));
		const resp = await page.request.get(
			`${API_BASE}/api/audit/verify-signatures?start=2026-01-01&end=2026-03-31`,
			{
				headers: {
					Authorization: `Bearer ${token}`,
					'X-Tenant-Slug': currentTenantSlug()
				}
			}
		);
		expect(resp.status()).toBe(403);
	});
});
