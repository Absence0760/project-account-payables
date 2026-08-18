import { API_BASE, authedTenantHeaders, expect, test } from '../fixtures/helpers';

/**
 * Segregation of duties on a UI-created workflow.
 *
 * Regression (Critical): `DEFAULT_APPROVAL_CONFIG` hardcoded
 * `require_segregation: false`, and both create paths (`/workflows` "+ New
 * Workflow" and the builder's Add-step) spread it verbatim. The backend defaults
 * the flag ON — `schemas/workflow.py` declares `require_segregation: bool = True`
 * and `services/approval_chain.py` reads `.get("require_segregation", True)`, so
 * an ABSENT key is safe — but an explicit `false` is a real value that switched
 * the control off. With no toggle anywhere in the UI it could neither be seen
 * nor re-enabled, so every workflow built in the builder permitted the uploader
 * to approve their own invoice. Template-created workflows were never affected.
 *
 * Two things are locked here: the PERSISTED default (what the backend stores and
 * enforces) and the VISIBLE toggle (so turning it off stays a deliberate act).
 */

interface WorkflowStep {
	type: string;
	name: string;
	enabled: boolean;
	config: Record<string, unknown>;
}

async function createWorkflow(page: import('@playwright/test').Page): Promise<string> {
	await page.goto('/workflows');
	await page.getByRole('button', { name: '+ New Workflow' }).click();
	await page.locator('#wf-name').fill(`Segregation E2E ${Date.now()}`);
	await page.getByRole('button', { name: /^Create$/ }).click();
	await page.waitForURL(/\/workflows\/[a-f0-9-]{36}/, { timeout: 10_000 });
	const id = page.url().match(/\/workflows\/([a-f0-9-]{36})/)![1];
	await expect(page.locator('.canvas .node').first()).toBeVisible();
	return id;
}

async function deleteWorkflow(page: import('@playwright/test').Page, id: string) {
	await page.request.delete(`${API_BASE}/api/workflows/${id}`, {
		headers: await authedTenantHeaders(page)
	});
}

async function approvalStep(
	page: import('@playwright/test').Page,
	id: string
): Promise<WorkflowStep> {
	const resp = await page.request.get(`${API_BASE}/api/workflows/${id}`, {
		headers: await authedTenantHeaders(page)
	});
	const wf = (await resp.json()) as { steps_config: { steps: WorkflowStep[] } };
	const step = wf.steps_config.steps.find((s) => s.type === 'approval');
	expect(step, 'the default pipeline must contain an approval step').toBeTruthy();
	return step!;
}

/** Select the approval step in the builder canvas so its config panel renders. */
async function selectApprovalStep(page: import('@playwright/test').Page) {
	await page.locator('.canvas .node').nth(1).click();
	await expect(page.locator('button#approval-segregation')).toBeVisible();
}

test.describe('/workflows approval-step segregation of duties', () => {
	test('a UI-created workflow PERSISTS require_segregation = true', async ({ page }) => {
		const id = await createWorkflow(page);
		try {
			const step = await approvalStep(page, id);
			// Not `.toBeTruthy()`: an absent key would also be "safe" thanks to the
			// backend default, but the client explicitly sends the flag, so assert
			// the exact stored value.
			expect(step.config.require_segregation).toBe(true);
		} finally {
			await deleteWorkflow(page, id);
		}
	});

	test('the approval-step editor shows the toggle, ON by default', async ({ page }) => {
		const id = await createWorkflow(page);
		try {
			await selectApprovalStep(page);
			const toggle = page.locator('button#approval-segregation');
			await expect(toggle).toHaveAttribute('aria-checked', 'true');
			await expect(toggle).toHaveClass(/on/);
		} finally {
			await deleteWorkflow(page, id);
		}
	});

	test('turning it OFF is deliberate, warns, and round-trips through PATCH', async ({ page }) => {
		const id = await createWorkflow(page);
		try {
			await selectApprovalStep(page);
			const toggle = page.locator('button#approval-segregation');

			await toggle.click();
			await expect(toggle).toHaveAttribute('aria-checked', 'false');
			// Switching the control off surfaces what it means, rather than doing it
			// silently the way the old hardcoded default did.
			await expect(
				page.getByText('can approve their own invoice', { exact: false })
			).toBeVisible();

			const saved = page.waitForResponse(
				(r) =>
					r.url().includes(`/api/workflows/${id}`) &&
					r.request().method() === 'PATCH' &&
					r.status() === 200
			);
			await page.locator('button.btn-save').click();
			await saved;

			expect((await approvalStep(page, id)).config.require_segregation).toBe(false);

			// And back on again — the control is genuinely two-way, which is the
			// half that was impossible before (there was no toggle at all).
			await page.reload();
			await selectApprovalStep(page);
			await expect(toggle).toHaveAttribute('aria-checked', 'false');
			await toggle.click();

			const savedAgain = page.waitForResponse(
				(r) =>
					r.url().includes(`/api/workflows/${id}`) &&
					r.request().method() === 'PATCH' &&
					r.status() === 200
			);
			await page.locator('button.btn-save').click();
			await savedAgain;

			expect((await approvalStep(page, id)).config.require_segregation).toBe(true);
		} finally {
			await deleteWorkflow(page, id);
		}
	});
});
