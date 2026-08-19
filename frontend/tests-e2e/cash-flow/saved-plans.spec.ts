import { expect, test } from '../fixtures/helpers';

/**
 * /cash-flow — saved plans + the consolidated (whole-group) scope.
 *
 * A saved plan is a FROZEN snapshot of one proposal, keyed by the same
 * deterministic `plan_id` the enact routes use (docs/cash-flow-copilot.md §5).
 * The side rail lists them and expands each into a plan-vs-actual comparison.
 *
 * Structural assertions only — the seed's money moves, and the point of these
 * tests is that the surface exists, is reachable, and completes rather than
 * hanging. Money correctness is proven exactly in
 * `backend/tests/test_cash_flow_saved_plans.py`, where it can be asserted to
 * the cent.
 */

test.describe('/cash-flow saved plans', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/cash-flow');
		await page.waitForLoadState('networkidle');
		await expect(page.getByRole('heading', { name: 'Cash-Flow Copilot' })).toBeVisible();
	});

	test('the side rail carries the saved-plans panel and the consolidated toggle', async ({
		page
	}) => {
		const panel = page.getByTestId('saved-plans-panel');
		await expect(panel).toBeVisible({ timeout: 10_000 });
		await expect(panel.getByText('Saved plans')).toBeVisible();

		// Consolidated mode answers for the whole legal group instead of the
		// entity selected in the sidebar; off by default.
		const toggle = page.getByRole('checkbox', { name: 'All entities' });
		await expect(toggle).toBeVisible();
		await expect(toggle).not.toBeChecked();
	});

	test('a proposed plan can be saved and then appears in the rail', async ({ page }) => {
		await page
			.locator('.prompt-btn', { hasText: 'Propose a payment plan for the next quarter' })
			.click();

		const planCard = page.locator('.msg.assistant').last().getByTestId('payment-plan-card');
		await expect(planCard).toBeVisible({ timeout: 15_000 });

		await planCard.getByRole('button', { name: 'Save plan' }).click();

		// Saved for the first time, or already saved by an earlier run against
		// this worker's tenant (the snapshot is keyed by a deterministic id that
		// includes today's date) — either way it resolves, never hangs.
		await expect(planCard.locator('.plan-action-result, .plan-action-error')).toBeVisible({
			timeout: 15_000
		});

		const panel = page.getByTestId('saved-plans-panel');
		await panel.getByRole('button', { name: 'Refresh' }).click();
		await expect(panel.locator('.saved-row')).not.toHaveCount(0, { timeout: 10_000 });
	});

	test('opening a saved plan renders its plan-vs-actual comparison', async ({ page }) => {
		await page
			.locator('.prompt-btn', { hasText: 'Propose a payment plan for the next quarter' })
			.click();

		const planCard = page.locator('.msg.assistant').last().getByTestId('payment-plan-card');
		await expect(planCard).toBeVisible({ timeout: 15_000 });
		await planCard.getByRole('button', { name: 'Save plan' }).click();
		await expect(planCard.locator('.plan-action-result, .plan-action-error')).toBeVisible({
			timeout: 15_000
		});

		const panel = page.getByTestId('saved-plans-panel');
		await panel.getByRole('button', { name: 'Refresh' }).click();
		const row = panel.locator('.saved-row').first();
		await expect(row).toBeVisible({ timeout: 10_000 });
		await row.click();

		// A plan saved today has no CLOSED period yet, so the honest readout is
		// "nothing to score" — not a fabricated zero variance.
		await expect(panel.locator('.saved-totals, .saved-error')).toBeVisible({ timeout: 10_000 });
	});
});
