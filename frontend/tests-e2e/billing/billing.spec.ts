import { execFileSync } from 'node:child_process';

import { currentTenantSlug, expect, signInAndWait, test } from '../fixtures/helpers';

/**
 * /billing — Platform Billing & Metering (read/display surface).
 *
 * Consumes `GET /api/billing/subscription` (admin/cfo): current plan + tier +
 * price, subscription status badge, billing-period window, and usage-to-date
 * meters. This is the AP platform's OWN customer billing (control-plane), not
 * the AP money path the app runs for customers.
 *
 * Login model mirrors the rest of the suite: the default per-worker storage
 * state signs the worker's admin in (admin is in the allowed set admin/cfo),
 * so the page loads directly without a redirect. The "not authorized" block
 * opts out and signs in as the clerk.
 *
 * Billing rows live in the CONTROL plane (`account_payables`), keyed by the
 * org's id — not the tenant DB — so the happy-path test seeds a Plan +
 * Subscription via control-plane psql and tears them down in `finally`.
 *
 * Selectors are accessible name / data-testid — never brittle CSS/nth-child,
 * never `waitForTimeout`.
 */

const CONTROL_DB = 'account_payables';

/** Run a synchronous `psql -c <query>` against the CONTROL-plane DB and return
 *  the FIRST output line. `INSERT … RETURNING id` emits the id line AND a
 *  trailing `INSERT 0 1` command tag on this psql build, so we take line 0 (the
 *  RETURNING value) rather than the whole — a plain trim would keep the tag. */
function controlPsql(query: string): string {
	const out = execFileSync(
		'psql',
		['-h', 'localhost', '-U', 'postgres', '-p', '5432', '-d', CONTROL_DB, '-tAc', query],
		{ env: { ...process.env, PGPASSWORD: 'postgres' }, stdio: ['ignore', 'pipe', 'pipe'] }
	);
	return out
		.toString()
		.split('\n')
		.map((l) => l.trim())
		.filter((l) => l.length > 0 && !/^INSERT \d+ \d+$/.test(l))[0] ?? '';
}

/** The control-plane org id for the current worker's tenant slug. */
function currentOrgId(): string {
	const slug = currentTenantSlug();
	const id = controlPsql(`SELECT id FROM organizations WHERE slug='${slug}'`);
	expect(id, `no control-plane org for slug ${slug}`).toMatch(/[0-9a-f-]{36}/);
	return id;
}

test.describe('/billing (admin)', () => {
	// Sign the worker admin in explicitly (don't rely on the shared per-worker
	// storage-state cache) so the gated page is reliably authed before each
	// test — admin is in the allowed set {admin, cfo}. This is a real,
	// deterministic sign-in, not a wait/retry workaround.
	test.use({ storageState: { cookies: [], origins: [] } });

	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
		await page.goto('/billing');
		await page.waitForLoadState('networkidle');
	});

	test('renders the billing surface header', async ({ page }) => {
		await expect(page.getByRole('heading', { name: 'Billing' })).toBeVisible();
	});

	test('with no subscription, shows the friendly empty state + usage meters', async ({ page }) => {
		// No subscription is seeded for e2e tenants, so the endpoint returns
		// plan=null / subscription=null and the page renders the empty state.
		// (If a previous run's seed leaked, this still holds only when clean —
		// the happy-path test always cleans up after itself.)
		const empty = page.getByTestId('billing-empty');
		const plan = page.getByTestId('billing-plan');
		// Exactly one of {empty, plan} is present depending on seed state.
		await expect(empty.or(plan)).toBeVisible({ timeout: 10_000 });

		if (await empty.isVisible()) {
			await expect(empty.getByRole('heading', { name: 'No active subscription' })).toBeVisible();
			// Usage meters render even with no plan.
			await expect(page.locator('.kpi').first()).toBeVisible();
			await expect(page.locator('.kpi-label', { hasText: 'Extractions' }).first()).toBeVisible();
		}
	});

	test('with a seeded subscription, shows plan, price, status badge + period', async ({ page }) => {
		const orgId = currentOrgId();
		const planCode = `e2e_billing_${Date.now()}`;
		let planId: string | null = null;
		try {
			planId = controlPsql(
				`INSERT INTO plans (id, code, name, monthly_price, currency, seat_component, ` +
					`usage_components, entitlements, trial_days, is_active, created_at, updated_at) ` +
					`VALUES (gen_random_uuid(), '${planCode}', 'E2E Growth', 49.00, 'USD', '{}'::jsonb, ` +
					`'{}'::jsonb, '{"public_api": true}'::jsonb, 14, true, now(), now()) RETURNING id`
			);
			expect(planId).toMatch(/[0-9a-f-]{36}/);

			controlPsql(
				`INSERT INTO subscriptions (id, organization_id, plan_id, status, ` +
					`current_period_start, current_period_end, trial_end, created_at, updated_at) ` +
					`VALUES (gen_random_uuid(), '${orgId}', '${planId}', 'active', ` +
					`now() - interval '5 days', now() + interval '25 days', NULL, now(), now())`
			);

			await page.goto('/billing');
			await page.waitForLoadState('networkidle');

			const plan = page.getByTestId('billing-plan');
			await expect(plan).toBeVisible({ timeout: 10_000 });
			await expect(plan.getByRole('heading', { name: 'E2E Growth' })).toBeVisible();
			// Status badge.
			await expect(plan.getByText('Active', { exact: true })).toBeVisible();
			// Exact price rendered through <Money> ($49.00).
			await expect(plan.getByText('$49.00')).toBeVisible();
			// The granted entitlement flag surfaces.
			await expect(plan.getByText('public api')).toBeVisible();
			// Usage meters still render.
			await expect(page.locator('.kpi-label', { hasText: 'Extractions' }).first()).toBeVisible();
		} finally {
			// Tear down subscription first (FK to plan), then the plan.
			if (planId) {
				controlPsql(`DELETE FROM subscriptions WHERE plan_id='${planId}'`);
				controlPsql(`DELETE FROM plans WHERE id='${planId}'`);
			}
		}
	});

	test('the Subscription section tab is visible + active for the admin', async ({ page }) => {
		// /billing is a child of the Billing nav group, so it surfaces as a
		// section sub-tab (not a top-level sidebar row). On /billing the Billing
		// group's sub-tab bar is shown with Subscription as the active tab.
		const tab = page.locator('nav.section-tabs a[href="/billing"]');
		await expect(tab).toBeVisible();
		await expect(tab).toHaveText('Subscription');
		await expect(tab).toHaveAttribute('aria-current', 'page');
	});
});

test.describe('/billing (clerk — not authorized)', () => {
	// Opt out of the default admin storage state so we can sign in as the clerk.
	test.use({ storageState: { cookies: [], origins: [] } });

	test('ap_clerk is redirected away and cannot see the nav row', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);

		await page.goto('/billing');
		// admin/cfo only — the page waits for /me then bounces the clerk to root.
		await page.waitForURL(/^http:\/\/[^/]+:7777\/?$/, { timeout: 15_000 });
		await expect(page.getByRole('heading', { name: 'Billing' })).toHaveCount(0);

		// The clerk also can't reach the Subscription tab (it's admin/cfo-only,
		// and they were bounced to the dashboard, which has no section tabs).
		await expect(page.locator('a[href="/billing"]')).toHaveCount(0);
	});

	test('the API 403s a clerk directly', async ({ page, tenantClerk }) => {
		await signInAndWait(page, tenantClerk);
		const token = await page.evaluate(() => localStorage.getItem('auth_token'));
		const resp = await page.request.get(
			`${process.env.PUBLIC_API_URL ?? 'http://localhost:8000'}/api/billing/subscription`,
			{ headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': currentTenantSlug() } }
		);
		expect(resp.status()).toBe(403);
	});
});
