import { expect, test } from '../fixtures/helpers';

/**
 * /workflows/[id] — pipeline editor.
 *
 * Each test navigates from the list page so the URL gets resolved
 * against the actual seeded workflow id (UUIDs aren't fixed by the
 * seed for workflows, only for users / orgs).
 *
 * IMPORTANT: these tests run against the seeded "Default Workflow".
 * Tests that *mutate* state live in lifecycle.spec.ts so they can
 * clean up after themselves; this file is read-only assertions
 * against the editor's render contract.
 *
 * Selectors reference the WorkflowCanvas component structure:
 *   - step nodes: `.canvas .node`   (was `.step-list .step-card`)
 *   - step type label: `.node-type` (was `.step-type`)
 *   - step number:    `.node-number`(was `.step-number`)
 *   - step name:      `.node-name`  (was `.step-name`)
 *   - add-step palette: `.palette`  (was `.add-step`)
 *   - page heading: `h1.page-title` (was `h2.page-title`)
 */

test.describe('/workflows/[id] editor (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/workflows');
		await page.locator('table tbody tr').first().getByRole('link', { name: 'Edit' }).click();
		await page.waitForURL(/\/workflows\/[a-f0-9-]+/);
	});

	test('header shows the workflow name and Default badge', async ({ page }) => {
		await expect(page.locator('h1.page-title')).toContainText('Default Workflow');
		await expect(page.locator('h1.page-title .default-badge')).toBeVisible();
	});

	test('toolbar has Active toggle (initially Active) and disabled Save button', async ({
		page
	}) => {
		const active = page.locator('button.btn-toggle.active');
		await expect(active).toHaveText('Active');

		// Save starts disabled because the editor isn't dirty yet.
		await expect(page.locator('button.btn-save')).toBeDisabled();
	});

	test('pipeline shows the three seeded steps in order', async ({ page }) => {
		const steps = page.locator('.canvas .node');
		await expect(steps).toHaveCount(3);

		// Step type labels should read in pipeline order.
		await expect(steps.nth(0).locator('.node-type')).toHaveText('Data Extraction');
		await expect(steps.nth(1).locator('.node-type')).toHaveText('Approval');
		await expect(steps.nth(2).locator('.node-type')).toHaveText('ERP Export');

		// Step numbers cascade 1 → 3.
		await expect(steps.nth(0).locator('.node-number')).toHaveText('1');
		await expect(steps.nth(1).locator('.node-number')).toHaveText('2');
		await expect(steps.nth(2).locator('.node-number')).toHaveText('3');
	});

	test('first step is selected by default and renders its config', async ({ page }) => {
		await expect(page.locator('.canvas .node').first()).toHaveClass(/selected/);
		// The right panel shows the selected step's heading + name input.
		await expect(page.locator('.config-header h3')).toContainText('Data Extraction');
		await expect(page.locator('input#step-name')).toHaveValue('Data Extraction');
	});

	test('clicking another step in the pipeline switches the config panel', async ({
		page
	}) => {
		await page.locator('.canvas .node').nth(1).click();
		await expect(page.locator('.canvas .node').nth(1)).toHaveClass(/selected/);
		await expect(page.locator('.config-header h3')).toContainText('Approval');
	});

	test('editing the description marks the form dirty and enables Save', async ({
		page
	}) => {
		// Save starts disabled.
		const save = page.locator('button.btn-save');
		await expect(save).toBeDisabled();

		await page.locator('input.desc-input').fill(`Edited ${Date.now()}`);
		await expect(save).toBeEnabled({ timeout: 3_000 });
	});

	test('add-step buttons are present for every step type', async ({ page }) => {
		// The palette sidebar has one button per step type.
		const palette = page.locator('.palette');
		await expect(palette).toBeVisible();
		await expect(palette.getByRole('button', { name: /Extraction/ })).toBeVisible();
		// Exact: /Approval/ also matches the "Parallel Approval" step button.
		await expect(palette.getByRole('button', { name: 'Add Approval step', exact: true })).toBeVisible();
		await expect(palette.getByRole('button', { name: /ERP Export/ })).toBeVisible();
	});

	test('back-link returns to the list page', async ({ page }) => {
		await page.locator('a.back-link').click();
		await page.waitForURL(/\/workflows$/);
		await expect(page.getByRole('heading', { name: 'Workflows' })).toBeVisible();
	});
});
