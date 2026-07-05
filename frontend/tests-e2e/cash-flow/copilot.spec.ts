import { expect, test } from '../fixtures/helpers';

/**
 * /cash-flow — AI Cash-Flow Copilot (Phase 1: read-only cash Q&A).
 *
 * The default storage state signs the worker's admin in — a finance leader, so
 * the copilot tools (admin/ap_manager/cfo only) are permitted. The backend's
 * mock assistant adapter is the local-first default and deterministic: the
 * copilot's own example prompts route to the new cash-flow tools, and
 * "cash position" / "run low on cash" resolve to `get_cash_position` (which the
 * page renders as the dedicated running-balance chart).
 *
 * We assert structure (an answer renders, the routed tool's chart/table
 * renders, the usage meter shows) rather than exact dollar amounts, which the
 * seed may evolve. Assertions hold for BOTH execution paths — the page streams
 * from `/copilot/stream` when available and falls back to `/copilot` otherwise —
 * because the rendered DOM is identical once the turn completes.
 */

test.describe('/cash-flow', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/cash-flow');
		await page.waitForLoadState('networkidle');
		await expect(page.getByRole('heading', { name: 'Cash-Flow Copilot' })).toBeVisible();
	});

	test('empty state shows the built-in example prompts + usage meter', async ({ page }) => {
		// The usage meter is fetched on mount, independent of any turn.
		await expect(page.getByTestId('usage-meter')).toBeVisible({ timeout: 10_000 });

		await expect(page.locator('.prompt-btn')).toHaveCount(3);
		await expect(
			page.locator('.prompt-btn', { hasText: 'When are we going to run low on cash?' })
		).toBeVisible();
		await expect(
			page.locator('.prompt-btn', { hasText: "What's our cash position over the next 90 days?" })
		).toBeVisible();
		await expect(
			page.locator('.prompt-btn', { hasText: 'Which discounts should I capture to save the most?' })
		).toBeVisible();
	});

	test('a cash-position question renders an answer + the running-balance chart', async ({ page }) => {
		await page
			.locator('.prompt-btn', { hasText: "What's our cash position over the next 90 days?" })
			.click();

		// The prompt becomes a user bubble.
		await expect(page.locator('.msg.user').last()).toContainText('cash position', {
			timeout: 15_000
		});

		// The assistant answers, and the cash-position tool routes to the dedicated
		// running-balance chart (data-testid, not the generic tool-result card).
		const assistant = page.locator('.msg.assistant').last();
		await expect(assistant).toBeVisible({ timeout: 15_000 });
		await expect(assistant.getByTestId('cash-position-chart')).toBeVisible({ timeout: 15_000 });

		// The composer is re-enabled once the turn settles.
		await expect(page.getByLabel('Ask the cash-flow copilot')).toBeEnabled({ timeout: 15_000 });
	});

	test('a discount-capture question renders the optimizer tool result', async ({ page }) => {
		await page
			.locator('.prompt-btn', { hasText: 'Which discounts should I capture to save the most?' })
			.click();

		const assistant = page.locator('.msg.assistant').last();
		await expect(assistant).toBeVisible({ timeout: 15_000 });
		// optimize_discount_capture has no bespoke view yet → the assistant's
		// tool-result card (carrying its data-tool) renders the structured output.
		await expect(
			assistant.locator('.tool-result[data-tool="optimize_discount_capture"]')
		).toBeVisible({ timeout: 15_000 });
	});

	test('typing a free-form cash question in the composer works', async ({ page }) => {
		const box = page.getByLabel('Ask the cash-flow copilot');
		await box.fill('when will we run low on cash?');
		await page.getByRole('button', { name: 'Send' }).click();

		await expect(page.locator('.msg.user').last()).toContainText('run low on cash', {
			timeout: 15_000
		});
		await expect(
			page.locator('.msg.assistant').last().getByTestId('cash-position-chart')
		).toBeVisible({ timeout: 15_000 });
	});

	test('New chat resets the conversation back to the empty state', async ({ page }) => {
		await page
			.locator('.prompt-btn', { hasText: 'When are we going to run low on cash?' })
			.click();
		await expect(page.locator('.msg.assistant').last()).toBeVisible({ timeout: 15_000 });
		await expect(page.getByLabel('Ask the cash-flow copilot')).toBeEnabled({ timeout: 15_000 });

		await page.getByRole('button', { name: 'New chat' }).click();

		await expect(page.locator('.prompt-btn')).toHaveCount(3);
		await expect(page.locator('.msg')).toHaveCount(0);
	});
});
