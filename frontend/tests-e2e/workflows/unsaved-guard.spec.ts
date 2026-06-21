import { expect, test } from '../fixtures/helpers';

/**
 * Workflow builder guards unsaved changes.
 *
 * The builder tracked a `dirty` flag but had no navigation guard, so clicking
 * away (or reloading) silently discarded all unsaved canvas edits. Navigating
 * away while dirty now prompts a confirm; dismissing it keeps you on the page.
 */
test.describe('workflow builder unsaved-changes guard', () => {
	test('navigating away while dirty prompts, and dismiss keeps you on the builder', async ({
		page
	}) => {
		// Reach a real workflow via the list (seeded per tenant).
		await page.goto('/workflows');
		const openLink = page.getByRole('button', { name: /Edit workflow|Open workflow/i }).first();
		// Fall back to the first clickable row link if the aria naming differs.
		const target = (await openLink.count()) ? openLink : page.locator('tbody tr a, tbody tr button').first();
		await target.click();
		await expect(page).toHaveURL(/\/workflows\/[0-9a-f-]+/);

		// Dirty the definition by editing its name.
		await page.getByRole('button', { name: /Edit .*name/i }).click();
		const nameInput = page.getByRole('textbox', { name: /workflow name/i });
		await nameInput.fill('Dirtied Workflow Name');
		await nameInput.blur();
		// Save enabling is the visible proof the edit registered as dirty.
		await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();

		// Attempt to navigate away → a confirm dialog must appear. Dismiss it.
		let dialogShown = false;
		page.on('dialog', (d) => {
			dialogShown = true;
			void d.dismiss();
		});
		await page.getByRole('link', { name: /back to workflows|workflows/i }).first().click();

		await expect.poll(() => dialogShown).toBe(true);
		// Dismissed → navigation cancelled, still on the builder.
		await expect(page).toHaveURL(/\/workflows\/[0-9a-f-]+/);
	});
});
