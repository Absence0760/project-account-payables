import { expect, test } from '../fixtures/helpers';

/**
 * /assistant — Conversational AP Assistant web UI.
 *
 * The default storage state signs the worker's admin in, so admin reaches the
 * page (the assistant is open to all four employee roles). The backend's mock
 * assistant adapter is the local-first default and is deterministic: a given
 * prompt routes to exactly one of the five fixed tools. We assert structure
 * (an answer renders, the routed tool's chart/table renders, the usage meter
 * shows) rather than exact dollar amounts, which the seed may evolve.
 *
 * Assertions are robust to BOTH execution paths — the page streams from
 * `/chat/stream` when available and falls back to `/chat` otherwise — because
 * the rendered DOM is identical once the turn completes.
 */

test.describe('/assistant', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/assistant');
		await page.waitForLoadState('networkidle');
		await expect(page.getByRole('heading', { name: 'AI Assistant' })).toBeVisible();
	});

	test('empty state shows the three built-in example prompts + usage meter', async ({ page }) => {
		// The usage meter is fetched on mount and is independent of any turn.
		await expect(page.getByTestId('usage-meter')).toBeVisible({ timeout: 10_000 });

		// The empty state offers exactly the three roadmap prompts, verbatim.
		await expect(page.locator('.prompt-btn')).toHaveCount(3);
		await expect(
			page.locator('.prompt-btn', { hasText: 'which approvals have I been sitting on > 5 days?' })
		).toBeVisible();
		await expect(
			page.locator('.prompt-btn', { hasText: 'which vendors are we paying the most this quarter?' })
		).toBeVisible();
		await expect(
			page.locator('.prompt-btn', { hasText: 'show me invoices with PO mismatches over $10k' })
		).toBeVisible();
	});

	test('clicking the pending-approvals prompt sends it and renders an answer + table', async ({
		page
	}) => {
		// Scope to `.prompt-btn` — a prior turn in the same worker leaves a
		// like-named entry in the conversation rail, so the bare accessible
		// name is ambiguous.
		await page
			.locator('.prompt-btn', { hasText: 'which approvals have I been sitting on > 5 days?' })
			.click();

		// The prompt becomes a user bubble.
		await expect(page.locator('.msg.user').last()).toContainText(
			'which approvals have I been sitting on',
			{ timeout: 15_000 }
		);

		// The assistant answers (prose or an inline error would both populate the
		// assistant bubble; a successful turn renders the routed tool result).
		const assistant = page.locator('.msg.assistant').last();
		await expect(assistant).toBeVisible({ timeout: 15_000 });

		// This prompt routes to list_pending_approvals → a table tool-result.
		await expect(
			assistant.locator('.tool-result[data-tool="list_pending_approvals"]')
		).toBeVisible({ timeout: 15_000 });

		// The composer is re-enabled once the turn settles (Send stays disabled
		// while the box is empty — that's the correct guard, so assert the box).
		await expect(page.getByLabel('Message the assistant')).toBeEnabled({ timeout: 15_000 });
	});

	test('vendor-spend prompt renders a bar chart from structured tool output', async ({ page }) => {
		await page
			.locator('.prompt-btn', { hasText: 'which vendors are we paying the most this quarter?' })
			.click();

		const assistant = page.locator('.msg.assistant').last();
		await expect(assistant).toBeVisible({ timeout: 15_000 });

		// get_vendor_spend renders a SpendBarChart inside its tool-result card.
		const spendCard = assistant.locator('.tool-result[data-tool="get_vendor_spend"]');
		await expect(spendCard).toBeVisible({ timeout: 15_000 });
		// Either bars (when the seed has spend) or the chart's empty message —
		// both prove the chart component rendered from the structured result.
		await expect(spendCard.locator('.chart-bars, .chart-empty').first()).toBeVisible();
	});

	test('typing a message in the composer and sending works', async ({ page }) => {
		const box = page.getByLabel('Message the assistant');
		await box.fill('show me approved invoices');
		await page.getByRole('button', { name: 'Send' }).click();

		await expect(page.locator('.msg.user').last()).toContainText('show me approved invoices', {
			timeout: 15_000
		});
		// list_invoices → a table tool-result.
		await expect(
			page.locator('.msg.assistant').last().locator('.tool-result[data-tool="list_invoices"]')
		).toBeVisible({ timeout: 15_000 });
	});

	test('New chat resets the conversation back to the empty state', async ({ page }) => {
		await page
			.locator('.prompt-btn', { hasText: 'which vendors are we paying the most this quarter?' })
			.click();
		await expect(page.locator('.msg.assistant').last()).toBeVisible({ timeout: 15_000 });
		await expect(page.getByLabel('Message the assistant')).toBeEnabled({ timeout: 15_000 });

		await page.getByRole('button', { name: '+ New chat' }).click();

		// Empty state is back — the example prompts reappear and there are no
		// message bubbles.
		await expect(page.locator('.prompt-btn')).toHaveCount(3);
		await expect(page.locator('.msg')).toHaveCount(0);
	});
});
