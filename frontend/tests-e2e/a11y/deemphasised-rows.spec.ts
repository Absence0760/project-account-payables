import { expect, test } from '../fixtures/helpers';
import { expectNoA11yViolations } from './axe-helper';

/**
 * Accessibility regression guard for **de-emphasised list rows** (WCAG 1.4.3).
 *
 * A paused webhook subscription, a revoked API key and a deactivated user are
 * each drawn quieter than their live siblings. That used to be spelled
 * `opacity: 0.5`–`0.6` on the row's cells, which is GROUP opacity: it
 * composites the row's whole subtree — text, status badge and all — down onto
 * the surface behind it, so it faded hardest exactly the colours that were
 * already quiet. Measured on `--surface`:
 *
 *     --text            @0.6 → 5.65:1   @0.5 → 4.34:1
 *     --text-muted      @0.6 → 2.77:1   @0.5 → 2.33:1
 *     a tinted <Badge>  @0.6 → 2.78–2.93:1
 *
 * i.e. the one cell explaining *why* a row is faded became the least readable
 * thing in it. The rows now take `class:row-muted` (`app.css`), which names
 * `--text-muted` (5.38:1) and leaves a descendant that sets its own colour —
 * the badge's calibrated `-on-tint` pair — at full strength.
 *
 * **Why this spec exists separately from `axe.spec.ts`.** That suite scans
 * these same routes, and it passed throughout: a fresh `e2e<N>` tenant has no
 * paused subscription, no revoked key and no deactivated user, so the faded row
 * never rendered and axe had nothing to measure. A route list is not coverage
 * of a route's *states*. Each test here stubs the list response so the
 * de-emphasised row is guaranteed on screen when axe runs.
 *
 * The complement is the static scan `src/lib/a11y/opacityAudit.test.ts`, which
 * catches the idiom being reintroduced anywhere in the tree without needing a
 * route to render it. Neither subsumes the other: the scan cannot resolve the
 * cascade, and axe cannot see a state no listed route reaches.
 */

const ISO = '2026-01-15T09:00:00Z';

/** One live row + one de-emphasised row, so the scan covers both treatments. */
const SUBSCRIPTIONS = [
	{
		id: 'sub-active',
		name: 'Ledger sync',
		target_url: 'https://example.test/hooks/ledger',
		event_types: ['invoice.approved'],
		secret_prefix: 'whsec_liv',
		active: true,
		created_at: ISO,
		updated_at: ISO,
		previous_secret_expires_at: null
	},
	{
		id: 'sub-paused',
		name: 'Archive mirror',
		target_url: 'https://example.test/hooks/archive',
		event_types: ['payment.settled', 'exception.raised'],
		secret_prefix: 'whsec_pau',
		active: false,
		created_at: ISO,
		updated_at: ISO,
		previous_secret_expires_at: null
	}
];

const API_KEYS = [
	{
		id: 'key-active',
		name: 'Reporting pipeline',
		key_prefix: 'feoh_live_aaa',
		scopes: ['read'],
		created_at: ISO,
		last_used_at: ISO,
		revoked_at: null
	},
	{
		id: 'key-revoked',
		name: 'Retired integration',
		key_prefix: 'feoh_live_zzz',
		scopes: ['read'],
		created_at: ISO,
		last_used_at: ISO,
		revoked_at: ISO
	}
];

const USERS = {
	items: [
		{
			id: 'user-active',
			email: 'live.user@example.test',
			full_name: 'Live User',
			is_active: true,
			roles: [{ id: 'role-1', name: 'ap_manager' }],
			created_at: ISO,
			last_login: ISO
		},
		{
			id: 'user-inactive',
			email: 'former.user@example.test',
			full_name: 'Former User',
			is_active: false,
			roles: [{ id: 'role-2', name: 'ap_clerk' }],
			created_at: ISO,
			last_login: null
		}
	],
	total: 2,
	page: 1,
	page_size: 20
};

/**
 * Fulfil `pathname` with `body`, letting every other URL through. The
 * `**${pathname}*` glob also matches nested routes (`/api/webhooks/deliveries`),
 * so the handler re-checks the pathname rather than trusting the glob — the
 * same guard `tests-e2e/requisitions/search-scope.spec.ts` documents.
 */
async function stubList(
	page: import('@playwright/test').Page,
	pathname: string,
	body: unknown
): Promise<void> {
	await page.route(`**${pathname}*`, async (route) => {
		if (new URL(route.request().url()).pathname !== pathname) {
			await route.continue();
			return;
		}
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(body)
		});
	});
}

test.describe('accessibility — de-emphasised rows (WCAG 1.4.3)', () => {
	test('a paused webhook subscription row has no axe violations', async ({ page }) => {
		await stubList(page, '/api/webhooks', SUBSCRIPTIONS);
		await page.goto('/admin/webhooks');
		await expect(page.getByRole('heading', { name: 'Webhooks', exact: true })).toBeVisible();

		// The de-emphasised row must actually be on screen — otherwise this
		// passes for the same reason the route-list scan did.
		const paused = page.locator('table tbody tr.row-muted');
		await expect(paused).toHaveCount(1);
		await expect(paused).toContainText('Archive mirror');

		await expectNoA11yViolations(page);
	});

	test('a revoked API-key row has no axe violations', async ({ page }) => {
		await stubList(page, '/api/api-keys', API_KEYS);
		await page.goto('/admin/api-keys');
		await expect(page.getByRole('heading', { name: 'API Keys', exact: true })).toBeVisible();

		const revoked = page.locator('table tbody tr.row-muted');
		await expect(revoked).toHaveCount(1);
		// The status cell is the one the old fade had to carve out with
		// `:not(.status-col)` — it must be inside the de-emphasised row here, so
		// axe measures the badge under whatever the row treatment now is.
		await expect(revoked).toContainText('Revoked');

		await expectNoA11yViolations(page);
	});

	test('a deactivated user row has no axe violations', async ({ page }) => {
		await stubList(page, '/api/admin/users', USERS);
		await page.goto('/admin');
		await expect(page.getByRole('heading', { name: 'Users & Roles', exact: true })).toBeVisible();

		const inactive = page.locator('table tbody tr.row-muted');
		await expect(inactive).toHaveCount(1);
		// The email cell is `--text-muted` at rest — 2.33:1 under the old 0.5
		// fade, and the reason this row is scanned rather than assumed fine.
		await expect(inactive).toContainText('former.user@example.test');

		await expectNoA11yViolations(page);
	});
});
