import {
	API_BASE,
	authedTenantHeaders,
	deleteWorkflowsWhere,
	expect,
	test
} from '../fixtures/helpers';

/**
 * Workflow lifecycle — create, delete, activate. These tests *mutate*
 * tenant state, so they're carefully scoped to their own
 * timestamp-suffixed workflows and clean up in finally blocks. The
 * seeded "Default Workflow" is never deleted; the only mutation that
 * touches it is `is_active` flipping in the activation-invariant test,
 * which the test reverses at the end.
 */

/**
 * Every workflow definition these tests create is named `${MARKER}…`, so the
 * `afterEach` below can sweep by name rather than by id.
 *
 * The `finally` blocks are not enough on their own here. Every test creates
 * its row through the UI and only enters its `try` once `waitForURL` has
 * handed back an id, so a create that lands the POST and then fails to settle
 * leaks the row; and the delete-via-the-list test has no `try` at all, so any
 * failure after the create leaks unconditionally. Sweeping by name needs no id.
 *
 * `deleteWorkflowsWhere` owns the walk below the definition — its three
 * non-cascading children — and the `is_default = false` seatbelt that keeps a
 * marker typo away from the seeded default.
 */
const MARKER = 'WF Lifecycle E2E ';

async function deleteWorkflowById(
	page: import('@playwright/test').Page,
	id: string
) {
	await page.request.delete(`${API_BASE}/api/workflows/${id}`, {
		headers: await authedTenantHeaders(page)
	});
}

async function patchWorkflow(
	page: import('@playwright/test').Page,
	id: string,
	body: Record<string, unknown>
) {
	return page.request.patch(`${API_BASE}/api/workflows/${id}`, {
		headers: await authedTenantHeaders(page),
		data: body
	});
}

async function listWorkflows(page: import('@playwright/test').Page) {
	const resp = await page.request.get(`${API_BASE}/api/workflows`, {
		headers: await authedTenantHeaders(page)
	});
	return (
		(await resp.json()) as {
			items: Array<{
				id: string;
				is_default: boolean;
				is_active: boolean;
			}>;
		}
	).items;
}

test.describe('workflow lifecycle', () => {
	test.afterEach(() => deleteWorkflowsWhere(MARKER));

	test('create-from-modal redirects to the detail page and adds a list row', async ({
		page
	}) => {
		const name = `${MARKER}Test Workflow ${Date.now()}`;
		await page.goto('/workflows');
		// The seeded default is always present, and matching on its name rules
		// out DataTable's loading placeholder <tr> — which `table tbody tr`
		// alone counts, and `count()` does no waiting of its own.
		await expect(page.locator('table tbody tr', { hasText: 'Default Workflow' })).toBeVisible();
		const beforeRows = await page.locator('table tbody tr').count();

		await page.getByRole('button', { name: '+ New Workflow' }).click();
		await page.locator('#wf-name').fill(name);
		await page.locator('#wf-desc').fill('Created by lifecycle e2e');
		await page.getByRole('button', { name: /^Create$/ }).click();

		// handleCreate runs window.location.href = '/workflows/<id>',
		// which kicks off a full nav. Reading the POST response body
		// during that nav is racy ("No resource with given identifier"),
		// so extract the new id from the URL after it lands.
		await page.waitForURL(/\/workflows\/[a-f0-9-]{36}/, { timeout: 10_000 });
		const match = page.url().match(/\/workflows\/([a-f0-9-]{36})/);
		const newId = match![1];

		try {
			await expect(page.locator('h1.page-title')).toContainText(name);

			// Newly-created workflows start `is_active=False` per the
			// API contract. Toggle button should reflect that.
			await expect(page.locator('button.btn-toggle')).toHaveText('Inactive');

			// Going back to the list shows the new row.
			await page.goto('/workflows');
			// The new row's own assertion runs FIRST: it auto-waits, and it is
			// the signal that the list this test counts has actually landed.
			await expect(
				page.locator('table tbody tr', { hasText: name })
			).toBeVisible();
			expect(await page.locator('table tbody tr').count()).toBe(beforeRows + 1);
		} finally {
			await deleteWorkflowById(page, newId);
		}
	});

	test('default workflow cannot be deleted: API returns 409', async ({ page }) => {
		const list = await listWorkflows(page);
		const defaultWf = list.find((w) => w.is_default);
		expect(defaultWf).toBeTruthy();

		const resp = await page.request.delete(
			`${API_BASE}/api/workflows/${defaultWf!.id}`,
			{ headers: await authedTenantHeaders(page) }
		);
		expect(resp.status()).toBe(409);
	});

	test('non-default workflow can be deleted via the list', async ({ page }) => {
		// Create a throwaway workflow, then delete via the list-row button.
		await page.goto('/workflows');
		const name = `${MARKER}Delete Me ${Date.now()}`;
		await page.getByRole('button', { name: '+ New Workflow' }).click();
		await page.locator('#wf-name').fill(name);
		await page.getByRole('button', { name: /^Create$/ }).click();
		await page.waitForURL(/\/workflows\/[a-f0-9-]{36}/);
		const newId = page.url().match(/\/workflows\/([a-f0-9-]{36})/)![1];

		// Bounce back to the list.
		await page.goto('/workflows');
		const row = page.locator('table tbody tr', { hasText: name });
		await expect(row).toBeVisible();

		const deleted = page.waitForResponse(
			(r) =>
				r.url().includes(`/api/workflows/${newId}`) &&
				r.request().method() === 'DELETE'
		);
		await row.locator('button.row-action.variant-danger').click();
		await deleted;
		await expect(row).toBeHidden({ timeout: 5_000 });
	});

	test('one-active invariant: activating a new workflow deactivates the seeded default', async ({
		page
	}) => {
		await page.goto('/workflows');
		const before = await listWorkflows(page);
		const defaultWf = before.find((w) => w.is_default)!;
		expect(defaultWf.is_active).toBe(true);

		const name = `${MARKER}Activate Me ${Date.now()}`;
		await page.getByRole('button', { name: '+ New Workflow' }).click();
		await page.locator('#wf-name').fill(name);
		await page.getByRole('button', { name: /^Create$/ }).click();
		await page.waitForURL(/\/workflows\/[a-f0-9-]{36}/);
		const newId = page.url().match(/\/workflows\/([a-f0-9-]{36})/)![1];

		try {
			// PATCH the new one to is_active=true. Triggers the
			// deactivate-others branch on the backend.
			const activate = await patchWorkflow(page, newId, { is_active: true });
			expect(activate.status()).toBe(200);
			const activated = await activate.json();
			expect(activated.is_active).toBe(true);

			// Re-fetch — the seeded default must now be inactive,
			// and exactly one workflow is active.
			const after = await listWorkflows(page);
			const defaultAfter = after.find((w) => w.id === defaultWf.id)!;
			expect(defaultAfter.is_active).toBe(false);
			expect(after.filter((w) => w.is_active).map((w) => w.id)).toEqual([newId]);
		} finally {
			// Cleanup: deactivate the test workflow, reactivate the
			// seeded default, delete the test workflow. Order matters —
			// activating the default while the test workflow is still
			// active would deactivate the test workflow but the test
			// workflow is what we're about to delete anyway.
			await patchWorkflow(page, newId, { is_active: false });
			await patchWorkflow(page, defaultWf.id, { is_active: true });
			await deleteWorkflowById(page, newId);
		}
	});
});
