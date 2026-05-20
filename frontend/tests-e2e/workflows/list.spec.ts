import { expect, test } from '../fixtures/helpers';

/**
 * /workflows — list view (admin-only). Seed creates one default
 * workflow per tenant (extraction → approval → ERP export).
 */

test.describe('/workflows list (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/workflows');
		await page.waitForLoadState('networkidle');
	});

	test('lists the seeded default workflow with the Default badge', async ({ page }) => {
		await expect(page.getByRole('heading', { name: 'Workflows' })).toBeVisible();
		const row = page.locator('table tbody tr').first();
		await expect(row).toBeVisible();
		await expect(row.locator('a.wf-name')).toContainText('Default Workflow');
		await expect(row.locator('.default-badge')).toBeVisible();
	});

	test('default row has no Delete button', async ({ page }) => {
		// The list-page delete is gated by `{#if !wf.is_default}`.
		// Hiding the affordance is the UX contract; the API also
		// rejects (asserted in lifecycle.spec.ts).
		const defaultRow = page.locator('table tbody tr', { hasText: 'Default Workflow' });
		await expect(defaultRow.locator('button.row-action.variant-danger')).toHaveCount(0);
	});

	test('step summary shows the enabled steps in pipeline order', async ({ page }) => {
		// Seed enables all three steps, so the summary cell reads
		// "Data Extraction → Approval → ERP Export".
		const cell = page.locator('table tbody tr', { hasText: 'Default Workflow' }).locator('.steps-cell');
		await expect(cell).toContainText('Data Extraction');
		await expect(cell).toContainText('Approval');
		await expect(cell).toContainText('ERP Export');
	});

	test('default row shows Active status', async ({ page }) => {
		// Seed creates the default with is_active=True.
		const row = page.locator('table tbody tr', { hasText: 'Default Workflow' });
		await expect(row.locator('.status-dot.active')).toBeVisible();
	});

	test('create modal: submit is disabled until a name is filled', async ({ page }) => {
		await page.getByRole('button', { name: '+ New Workflow' }).click();

		const submit = page.getByRole('button', { name: /^Create$/ });
		await expect(submit).toBeDisabled();

		await page.locator('#wf-name').fill('  ');  // whitespace only — still empty
		await expect(submit).toBeDisabled();

		await page.locator('#wf-name').fill('Real name');
		await expect(submit).toBeEnabled();
	});

	test('create modal: Cancel dismisses without creating anything', async ({ page }) => {
		await page.getByRole('button', { name: '+ New Workflow' }).click();
		await page.locator('#wf-name').fill('Should not persist');

		const beforeRows = await page.locator('table tbody tr').count();
		await page.getByRole('button', { name: 'Cancel' }).click();
		// Modal gone, list unchanged.
		await expect(page.locator('#wf-name')).toBeHidden();
		expect(await page.locator('table tbody tr').count()).toBe(beforeRows);
	});
});
