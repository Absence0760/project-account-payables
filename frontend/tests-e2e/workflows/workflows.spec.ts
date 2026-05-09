import { expect, test } from '@playwright/test';

import { signInAndWait } from '../fixtures/helpers';

/**
 * /workflows — admin-only surface. Seed creates one default workflow
 * per tenant ("Default Workflow") with extraction + approval +
 * erp_export steps enabled.
 */

test.describe('/workflows (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
		await page.goto('/workflows');
		await page.waitForLoadState('networkidle');
	});

	test('lists the seeded default workflow', async ({ page }) => {
		await expect(page.getByRole('heading', { name: 'Workflows' })).toBeVisible();
		await expect(
			page.locator('table tbody tr a.wf-name').first()
		).toBeVisible();

		// The seeded workflow is_default=true → renders a "Default" badge.
		await expect(page.locator('.default-badge').first()).toBeVisible();
	});

	test('opening the new-workflow modal renders the form', async ({ page }) => {
		await page.getByRole('button', { name: '+ New Workflow' }).click();
		// The form has fields for name + description.
		await expect(page.locator('#wf-name')).toBeVisible({ timeout: 5_000 });
		await expect(page.locator('#wf-desc')).toBeVisible();
	});
});
