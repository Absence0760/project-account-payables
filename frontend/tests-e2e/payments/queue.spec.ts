import { expect, test } from '@playwright/test';

import { signInAndWait } from '../fixtures/helpers';

/**
 * /payments Queue tab — selection, Review & Pay panel, payment-method
 * selector, and Clear. Stops short of clicking "Create Draft Run" so
 * we don't pollute the seed payment_runs table; that path is exercised
 * by the runs detail tests via the API.
 */

test.describe('/payments queue selection (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
		await page.goto('/payments');
		await page.waitForLoadState('networkidle');
		// Queue is the default tab, but be defensive in case a prior
		// test left a different tab active in shared state.
		await page.locator('.tab', { hasText: 'Queue' }).click();
	});

	test('queue table renders rows from /api/payments/queue', async ({ page }) => {
		// The seeded `approved` invoices should be ready to pay. If the
		// seed has zero queue items, the rest of the suite is moot, so
		// fail fast with a helpful message.
		const rows = page.locator('table tbody tr');
		await expect(rows.first()).toBeVisible({ timeout: 5_000 });
		expect(await rows.count()).toBeGreaterThan(0);
		// Header includes the checkbox column.
		await expect(page.locator('thead th.checkbox-col input[type="checkbox"]')).toBeVisible();
	});

	test('selecting a row reveals the pay-bar with count + total', async ({ page }) => {
		const firstRow = page.locator('table tbody tr').first();
		await firstRow.locator('input[type="checkbox"]').check();

		const payBar = page.locator('.pay-bar');
		await expect(payBar).toBeVisible();
		await expect(payBar.locator('.pay-bar-count')).toContainText('1 selected');
		// Pay-bar shows a currency-formatted total (USD format includes "$").
		await expect(payBar.locator('.pay-bar-count')).toContainText('$');
	});

	test('selecting all via header checkbox selects every queue row', async ({ page }) => {
		const total = await page.locator('table tbody tr').count();
		await page.locator('thead th.checkbox-col input[type="checkbox"]').check();

		const payBar = page.locator('.pay-bar');
		await expect(payBar).toBeVisible();
		await expect(payBar.locator('.pay-bar-count')).toContainText(`${total} selected`);
		// Every body checkbox is now checked.
		const bodyChecks = page.locator('table tbody tr input[type="checkbox"]');
		const checkCount = await bodyChecks.count();
		for (let i = 0; i < checkCount; i++) {
			await expect(bodyChecks.nth(i)).toBeChecked();
		}
	});

	test('Clear button drops selection and hides pay-bar', async ({ page }) => {
		await page.locator('table tbody tr').first().locator('input[type="checkbox"]').check();
		const payBar = page.locator('.pay-bar');
		await expect(payBar).toBeVisible();

		await payBar.getByRole('button', { name: 'Clear' }).click();
		await expect(payBar).toBeHidden();
		await expect(
			page.locator('table tbody tr').first().locator('input[type="checkbox"]')
		).not.toBeChecked();
	});

	test('Review & Pay opens the panel; method selects default to ACH', async ({ page }) => {
		await page.locator('table tbody tr').first().locator('input[type="checkbox"]').check();
		await page.locator('.pay-bar').getByRole('button', { name: 'Review & Pay' }).click();

		const reviewPanel = page.locator('.review-panel');
		await expect(reviewPanel).toBeVisible();
		await expect(reviewPanel.locator('.review-title')).toContainText('Payment Review');

		// One row per selected invoice; the method <select> defaults to "ach".
		const methodSelect = reviewPanel.locator('select.method-select').first();
		await expect(methodSelect).toBeVisible();
		await expect(methodSelect).toHaveValue('ach');

		// All four options exist (ach, wire, check, virtual_card).
		const optionValues = await methodSelect.locator('option').evaluateAll(
			(opts) => (opts as HTMLOptionElement[]).map((o) => o.value)
		);
		expect(optionValues).toEqual(['ach', 'wire', 'check', 'virtual_card']);
	});

	test('changing the payment-method select sticks while panel is open', async ({ page }) => {
		await page.locator('table tbody tr').first().locator('input[type="checkbox"]').check();
		await page.locator('.pay-bar').getByRole('button', { name: 'Review & Pay' }).click();

		const select = page.locator('.review-panel select.method-select').first();
		await select.selectOption('wire');
		await expect(select).toHaveValue('wire');
		// Switching it again confirms the binding isn't one-way.
		await select.selectOption('check');
		await expect(select).toHaveValue('check');
	});

	test('Review panel button label reflects selection size pluralization', async ({
		page
	}) => {
		// Select two rows so the button reads "2 Invoices" (plural).
		const rows = page.locator('table tbody tr');
		const total = await rows.count();
		test.skip(total < 2, 'Seed has <2 queue items; pluralization unreachable.');

		await rows.nth(0).locator('input[type="checkbox"]').check();
		await rows.nth(1).locator('input[type="checkbox"]').check();
		await page.locator('.pay-bar').getByRole('button', { name: 'Review & Pay' }).click();

		const executeBtn = page.locator('.review-panel .btn-execute');
		await expect(executeBtn).toContainText('2 Invoices');
	});
});
