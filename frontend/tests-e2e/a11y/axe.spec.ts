import { expect, test } from '../fixtures/helpers';
import { expectNoA11yViolations } from './axe-helper';

/**
 * Automated accessibility regression guard (WCAG 2.2 Level AA).
 *
 * Runs axe-core against the key surfaces of the app at the WCAG 2.0/2.1/2.2
 * A + AA tag set and asserts zero violations. This is the automated leg of the
 * conformance posture documented in `docs/accessibility.md` /
 * `docs/accessibility-vpat.md` — it catches the machine-detectable regressions
 * (missing labels, contrast, ARIA misuse, landmark/heading structure, target
 * size) so a future change can't silently undo the a11y fixes this initiative
 * landed. Manual screen-reader passes (VoiceOver / NVDA / TalkBack) cover the
 * criteria axe can't assert; see the docs.
 *
 * Pattern: reuses the per-worker `e2e<N>` tenant + storage-state fixtures from
 * `fixtures/helpers.ts` exactly like every other spec — the authenticated specs
 * inherit the default admin storage state; the two unauthenticated surfaces
 * (the AP login page and the supplier portal login) opt out of it.
 *
 * It is auto-discovered by the normal `pnpm test:e2e` run (the config's
 * `testDir: '.'` walks recursively), so CI catches a11y regressions on every
 * push. `pnpm test:e2e:a11y` (frontend/package.json) targets just this folder
 * for a fast focused run.
 */

/**
 * Authenticated surfaces — the core invoice → approve → pay flow plus a
 * representative sample of the rest of the app shell. Each navigates as the
 * worker's admin (default storage state) and asserts axe-clean.
 */
const AUTHED_ROUTES = [
	{ path: '/', name: 'dashboard' },
	{ path: '/invoices', name: 'invoices list' },
	{ path: '/vendors', name: 'vendors list' },
	{ path: '/payments', name: 'payments' },
	{ path: '/exceptions', name: 'exceptions queue' }
] as const;

test.describe('accessibility — authenticated app (WCAG 2.2 AA)', () => {
	for (const { path, name } of AUTHED_ROUTES) {
		test(`${name} (${path}) has no axe violations`, async ({ page }) => {
			await page.goto(path);
			// The sidebar means we're inside the authenticated app shell;
			// waiting on it ensures axe scans the rendered page, not a
			// mid-redirect / pre-hydration frame.
			await expect(page.locator('aside.sidebar').first()).toBeVisible();
			await expect(page).not.toHaveURL(/\/login/);
			await expectNoA11yViolations(page);
		});
	}

	test('invoice detail modal (open state) has no axe violations', async ({ page }) => {
		// The invoice detail modal is the most interactive authenticated
		// surface (dialog semantics, focus trap, labelled form fields, the
		// Activity timeline). Open it the same way the detail spec does —
		// click the first row's "Edit invoice …" RowLink — then scan.
		await page.goto('/invoices');
		await expect(page.locator('table tbody tr').first()).toBeVisible();
		await page.locator('table tbody tr').first().getByRole('button', { name: 'Edit' }).click();

		const modal = page.locator('div.modal[role="dialog"]');
		await expect(modal).toBeVisible();
		// Wait for the Activity timeline to load so the scan covers the fully
		// populated modal, not a loading frame.
		await expect(modal.locator('.line-items-title')).toHaveText('Line Items');

		await expectNoA11yViolations(page);
	});
});

test.describe('accessibility — unauthenticated surfaces (WCAG 2.2 AA)', () => {
	// These surfaces have no signed-in user: drop the default admin storage
	// state so the page renders its real unauthenticated UI.
	test.use({ storageState: { cookies: [], origins: [] } });

	test('AP login page (/login) has no axe violations', async ({ page }) => {
		await page.goto('/login');
		await page.waitForLoadState('networkidle');
		await expect(page.locator('input[type="email"]')).toBeVisible();
		await expectNoA11yViolations(page);
	});

	test('supplier portal login (/portal/login) has no axe violations', async ({ page }) => {
		await page.goto('/portal/login');
		await page.waitForLoadState('networkidle');
		await expect(page.locator('input[type="email"]')).toBeVisible();
		await expectNoA11yViolations(page);
	});
});
