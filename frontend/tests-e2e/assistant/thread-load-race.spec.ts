import { expect, test } from '../fixtures/helpers';

/**
 * Regression guard: /assistant used to lose a message — and overwrite an
 * unrelated one — if you sent while a saved thread was still loading.
 *
 * `openConversation` opened with `if (busy) return` but never SET `busy`; only
 * `send()` did. The composer therefore stayed live for the whole conversation
 * GET. Sending in that window pushed the user + placeholder-assistant bubbles
 * and captured the placeholder's array INDEX; the GET then replaced `messages`
 * wholesale, dropping both; the model's answer was then written into
 * `messages[index]` of the NEW array — landing on top of an unrelated
 * historical message rather than merely vanishing.
 *
 * Two changes, tested here through the one observable consequence: `busy` is
 * now held for the duration of the load (so the composer is genuinely closed
 * during the window), and the placeholder is resolved by identity so a
 * replaced array can never misdirect the write.
 *
 * `/api/assistant/conversations*` is mocked so the load can be held open
 * deterministically.
 */

const THREAD_ID = '22222222-2222-4222-8222-222222222222';

test.describe('/assistant — sending while a saved thread loads', () => {
	test('the composer is closed for the whole conversation load', async ({ page }) => {
		let releaseThread: () => void = () => {};
		const threadGate = new Promise<void>((resolve) => (releaseThread = resolve));

		await page.route('**/api/assistant/conversations**', async (route) => {
			const url = new URL(route.request().url());
			if (url.pathname === '/api/assistant/conversations') {
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify({
						items: [
							{
								id: THREAD_ID,
								title: 'Race Guard Thread',
								message_count: 2,
								created_at: '2026-07-01T00:00:00Z',
								updated_at: '2026-07-01T00:00:00Z'
							}
						]
					})
				});
				return;
			}
			if (url.pathname === `/api/assistant/conversations/${THREAD_ID}`) {
				await threadGate;
				await route.fulfill({
					status: 200,
					contentType: 'application/json',
					body: JSON.stringify({
						conversation: {
							id: THREAD_ID,
							title: 'Race Guard Thread',
							message_count: 2,
							created_at: '2026-07-01T00:00:00Z',
							updated_at: '2026-07-01T00:00:00Z'
						},
						messages: [
							{ role: 'user', content: 'HISTORIC QUESTION', tool_calls: [] },
							{ role: 'assistant', content: 'HISTORIC ANSWER', tool_calls: [] }
						]
					})
				});
				return;
			}
			await route.continue();
		});

		await page.goto('/assistant');
		await expect(page.getByRole('heading', { name: 'AI Assistant' })).toBeVisible();

		const composer = page.getByRole('textbox', { name: 'Message the assistant' });
		await expect(composer).toBeEnabled();

		// Open the saved thread; its GET hangs.
		await page.getByRole('button', { name: /Race Guard Thread/ }).click();

		// The window in which the old code let a send through.
		await expect(composer).toBeDisabled();

		releaseThread();

		// The thread lands, and the composer reopens.
		await expect(page.getByText('HISTORIC ANSWER')).toBeVisible();
		await expect(composer).toBeEnabled();
		// Nothing was displaced: both historical bubbles are intact.
		await expect(page.getByText('HISTORIC QUESTION')).toBeVisible();
	});
});
