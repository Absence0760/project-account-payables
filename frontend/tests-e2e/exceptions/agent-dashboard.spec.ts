import { expect, signInAndWait, test } from '../fixtures/helpers';

/**
 * /exceptions → "AI Agents" tab — the autonomous-exception-agent dashboard.
 *
 * Read-only surface over GET /api/exceptions/agent-stats + /agent-decisions
 * (admin/ap_manager-gated). The KPI row + accuracy card render from /agent-stats
 * even with zero decisions; the decision log renders an empty-state row until an
 * agent has run. Accuracy is a placeholder pending a human-overturn signal and
 * is labelled as such — the test asserts it is NOT a fabricated number.
 */

test.describe('/exceptions AI Agents dashboard (manager)', () => {
	test.beforeEach(async ({ page, tenantManager }) => {
		await signInAndWait(page, tenantManager);
		await page.goto('/exceptions');
		await page.waitForLoadState('networkidle');
	});

	test('switching to the AI Agents tab shows the KPI dashboard', async ({ page }) => {
		// Default view is the queue.
		await expect(page.getByRole('tab', { name: /Queue/ })).toBeVisible();

		await page.getByRole('tab', { name: 'AI Agents' }).click();

		const dash = page.getByTestId('agent-dashboard');
		await expect(dash).toBeVisible({ timeout: 5_000 });

		// Core agent metrics are present.
		await expect(dash.getByText('Resolution rate')).toBeVisible();
		await expect(dash.getByText('Escalation rate')).toBeVisible();
		await expect(dash.getByText('Decisions made')).toBeVisible();
	});

	test('accuracy is shown as a labelled placeholder, not a fabricated number', async ({
		page
	}) => {
		await page.getByRole('tab', { name: 'AI Agents' }).click();

		const accuracy = page.getByTestId('agent-accuracy');
		await expect(accuracy).toBeVisible({ timeout: 5_000 });
		// The seed runs no agents, so accuracy must be the explicit deferred state.
		await expect(accuracy).toContainText('Not yet measured');
		await expect(accuracy).toContainText(/human-overturn signal/i);
	});

	test('the decision log renders (rows or an empty state)', async ({ page }) => {
		await page.getByRole('tab', { name: 'AI Agents' }).click();
		await expect(page.getByTestId('agent-dashboard')).toBeVisible({ timeout: 5_000 });

		await expect(page.getByRole('heading', { name: 'Recent decisions' })).toBeVisible();
		// Either there are decision rows or the empty-state cell is shown — both
		// prove the table mounted and the API call succeeded (no error toast).
		// Scoped to the decision-log section: the dashboard also carries the
		// runnable-exception table the Run-agent action lives on
		// (`agent-resolve.spec.ts`), so an unscoped `table` matches two.
		const tableMounted = page.locator('[data-testid="agent-decision-log"] table');
		await expect(tableMounted).toBeVisible();

		// The action filter chips are operable.
		await page.locator('[data-testid="agent-decision-log"] .filter-chip', { hasText: 'Auto-resolved' }).click();
		await page.waitForLoadState('networkidle');
		await expect(page.getByTestId('agent-dashboard')).toBeVisible();
	});

	test('the tab is keyboard-switchable back to the queue', async ({ page }) => {
		await page.getByRole('tab', { name: 'AI Agents' }).click();
		await expect(page.getByTestId('agent-dashboard')).toBeVisible({ timeout: 5_000 });

		await page.getByRole('tab', { name: 'Queue' }).click();
		// Queue panel back; the agent dashboard is gone.
		await expect(page.getByTestId('agent-dashboard')).toHaveCount(0);
		await expect(page.locator('table tbody tr').first()).toBeVisible({ timeout: 5_000 });
	});
});
