import { expect, test } from '../fixtures/helpers';

/**
 * The exceptions Queue/AI-Agents tab is URL-backed.
 *
 * The code claimed (in a comment) the view was persisted in the URL but never
 * synced it, so switching to AI Agents and reloading dropped you back to
 * Queue. The active view now lives in ?view=agents and survives a reload.
 */
test.describe('exceptions view URL state', () => {
	test('switching to AI Agents writes ?view and survives a reload', async ({ page }) => {
		await page.goto('/exceptions');
		// Default is Queue — no view param.
		await expect(page).not.toHaveURL(/view=agents/);

		const agentsTab = page.getByRole('tab', { name: /agent/i });
		await agentsTab.click();
		await expect(page).toHaveURL(/view=agents/);
		await expect(agentsTab).toHaveAttribute('aria-selected', 'true');

		// Reload — the URL keeps ?view=agents, and the AI-Agents tab stays active
		// (previously it reset to Queue).
		await page.reload();
		await expect(page).toHaveURL(/view=agents/);
		await expect(page.getByRole('tab', { name: /agent/i })).toHaveAttribute(
			'aria-selected',
			'true'
		);

		// Switching back to Queue clears the param.
		await page.getByRole('tab', { name: /queue/i }).click();
		await expect(page).not.toHaveURL(/view=agents/);
	});
});
