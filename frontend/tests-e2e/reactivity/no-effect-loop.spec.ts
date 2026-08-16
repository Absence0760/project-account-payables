import { expect } from '@playwright/test';
import { test, signInAndWait } from '../fixtures/helpers';

/**
 * Regression guard for the URL-filter reactivity loop.
 *
 * Every list route below reflects its filter/search state into the URL via a
 * `syncUrl()` helper that reads the current URL and writes it with
 * `replaceState`. `syncUrl()` is called synchronously inside the status-filter
 * `$effect`, so reading the *reactive* `$page.url` there made the effect depend
 * on the very state it mutated — Svelte tripped `effect_update_depth_exceeded`
 * on mount (a console error + an abandoned effect). The fix untracked that
 * read; `syncUrl()`'s whole body is now untracked, since it is a writer of URL
 * state and never a dependency source (the same generalisation also stopped a
 * tracked `search` read firing an un-debounced load per keystroke — see
 * `search-debounce-race.spec.ts`). This spec fails if any route reintroduces a
 * tracked read there: the loop surfaces as a console error / pageerror on mount.
 */
const LOOP_RE = /effect_update_depth_exceeded|Maximum update depth exceeded/i;

const FILTER_ROUTES = [
	'/vendor-statements',
	'/budgets',
	'/requisitions',
	'/expenses',
	'/positive-pay',
	'/recurring',
	'/contracts',
	'/intake'
];

test.describe('URL-filter routes mount without a reactive effect loop', () => {
	for (const route of FILTER_ROUTES) {
		test(`${route} mounts without effect_update_depth_exceeded`, async ({ page }) => {
			const loopErrors: string[] = [];
			page.on('console', (msg) => {
				if (msg.type() === 'error' && LOOP_RE.test(msg.text())) loopErrors.push(msg.text());
			});
			page.on('pageerror', (err) => {
				if (LOOP_RE.test(err.message)) loopErrors.push(err.message);
			});

			// Admin sees every one of these routes.
			await signInAndWait(page);
			await page.goto(route);
			await page.waitForLoadState('networkidle');
			// PageHeader's <h1> being visible means the route mounted and its
			// filter $effect ran — any loop would already have thrown by now.
			await expect(page.locator('h1').first()).toBeVisible();

			expect(loopErrors, `effect loop on ${route}:\n${loopErrors.join('\n')}`).toEqual([]);
		});
	}
});
