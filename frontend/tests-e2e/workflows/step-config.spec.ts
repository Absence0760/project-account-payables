import { API_BASE, authedTenantHeaders, expect, test } from '../fixtures/helpers';

async function createWorkflow(page: import('@playwright/test').Page): Promise<string> {
	// Use the UI's create flow so we land on the detail page with the
	// pipeline editor primed. The create POST kicks off a window.location
	// redirect; we read the new id from the URL once it lands.
	await page.goto('/workflows');
	await page.getByRole('button', { name: '+ New Workflow' }).click();
	await page.locator('#wf-name').fill(`Step Config E2E ${Date.now()}`);
	await page.getByRole('button', { name: /^Create$/ }).click();
	await page.waitForURL(/\/workflows\/[a-f0-9-]{36}/, { timeout: 10_000 });
	const id = page.url().match(/\/workflows\/([a-f0-9-]{36})/)![1];
	// Editor ready — pipeline rendered.
	await expect(page.locator('.step-list .step-card').first()).toBeVisible();
	return id;
}

async function deleteWorkflow(page: import('@playwright/test').Page, id: string) {
	await page.request.delete(`${API_BASE}/api/workflows/${id}`, {
		headers: await authedTenantHeaders(page)
	});
}

async function getWorkflow(page: import('@playwright/test').Page, id: string) {
	const resp = await page.request.get(`${API_BASE}/api/workflows/${id}`, {
		headers: await authedTenantHeaders(page)
	});
	return (await resp.json()) as {
		id: string;
		name: string;
		steps_config: { steps: Array<{ type: string; name: string; enabled: boolean; config: Record<string, unknown> }> };
	};
}

/**
 * Per-step config edits round-trip through PATCH and reload. New
 * workflows clone the default 3-step pipeline (extraction → approval →
 * erp_export) so we can edit any step without an add/remove first.
 */

test.describe('/workflows/[id] step config', () => {
	test('renaming a step persists through PATCH and reload', async ({ page }) => {
		const id = await createWorkflow(page);

		try {
			// Edit the first step's name.
			const newName = `Renamed Extraction ${Date.now() % 100000}`;
			await page.locator('input#step-name').fill(newName);

			const saved = page.waitForResponse(
				(r) =>
					r.url().includes(`/api/workflows/${id}`) &&
					r.request().method() === 'PATCH' &&
					r.status() === 200
			);
			await page.locator('button.btn-save').click();
			await saved;

			// API now reflects the rename.
			const wf = await getWorkflow(page, id);
			expect(wf.steps_config.steps[0].name).toBe(newName);

			// Reload — left pipeline shows the new name.
			await page.reload();
			await page.waitForLoadState('networkidle');
			await expect(
				page.locator('.step-list .step-card').first().locator('.step-name')
			).toHaveText(newName);
		} finally {
			await deleteWorkflow(page, id);
		}
	});

	test('toggling Enabled on a step round-trips through PATCH', async ({ page }) => {
		const id = await createWorkflow(page);

		try {
			// First step starts enabled (the default config has all steps enabled).
			// Toggle it off, save, verify.
			const enabledToggle = page.locator('button#step-enabled');
			await expect(enabledToggle).toHaveClass(/on/);
			await enabledToggle.click();
			await expect(enabledToggle).not.toHaveClass(/on/);

			const saved = page.waitForResponse(
				(r) =>
					r.url().includes(`/api/workflows/${id}`) &&
					r.request().method() === 'PATCH' &&
					r.status() === 200
			);
			await page.locator('button.btn-save').click();
			await saved;

			const wf = await getWorkflow(page, id);
			expect(wf.steps_config.steps[0].enabled).toBe(false);
		} finally {
			await deleteWorkflow(page, id);
		}
	});

	test('Approval step: switching strategy to specific persists', async ({ page }) => {
		const id = await createWorkflow(page);

		try {
			// Click the Approval step to focus its config panel.
			await page.locator('.step-list .step-card').nth(1).click();
			await expect(page.locator('.config-header h3')).toContainText('Approval');

			// Strategy dropdown defaults to "manual"; flip to "specific".
			await page.locator('select#approver-strategy').selectOption('specific');

			const saved = page.waitForResponse(
				(r) =>
					r.url().includes(`/api/workflows/${id}`) &&
					r.request().method() === 'PATCH' &&
					r.status() === 200
			);
			await page.locator('button.btn-save').click();
			await saved;

			const wf = await getWorkflow(page, id);
			const approval = wf.steps_config.steps.find((s) => s.type === 'approval');
			expect((approval?.config as { approver_strategy?: string }).approver_strategy).toBe(
				'specific'
			);
		} finally {
			await deleteWorkflow(page, id);
		}
	});

	test('Extraction step: enabling auto-approve reveals threshold slider and persists', async ({
		page
	}) => {
		const id = await createWorkflow(page);

		try {
			// First step (Extraction) is selected by default.
			await expect(page.locator('.config-header h3')).toContainText('Data Extraction');

			// Auto-approve toggle.
			const auto = page.locator('button#auto-approve');
			await auto.click();
			await expect(auto).toHaveClass(/on/);
			// Slider becomes visible after toggle on.
			await expect(page.locator('input#threshold')).toBeVisible();

			const saved = page.waitForResponse(
				(r) =>
					r.url().includes(`/api/workflows/${id}`) &&
					r.request().method() === 'PATCH' &&
					r.status() === 200
			);
			await page.locator('button.btn-save').click();
			await saved;

			const wf = await getWorkflow(page, id);
			const extraction = wf.steps_config.steps.find((s) => s.type === 'extraction');
			expect((extraction?.config as { auto_approve_enabled?: boolean }).auto_approve_enabled).toBe(
				true
			);
		} finally {
			await deleteWorkflow(page, id);
		}
	});

	test('Save button is disabled when not dirty and enables on first edit', async ({
		page
	}) => {
		const id = await createWorkflow(page);

		try {
			const save = page.locator('button.btn-save');
			await expect(save).toBeDisabled();

			await page.locator('input#step-name').fill('Anything');
			await expect(save).toBeEnabled({ timeout: 3_000 });
		} finally {
			await deleteWorkflow(page, id);
		}
	});
});
