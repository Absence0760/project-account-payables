import { expect, test } from '@playwright/test';

import { signInAndWait } from '../fixtures/helpers';

/**
 * Workflow lifecycle — create, delete, activate. These tests *mutate*
 * tenant state, so they're carefully scoped to their own
 * timestamp-suffixed workflows and clean up in finally blocks. The
 * seeded "Default Workflow" is never deleted; the only mutation that
 * touches it is `is_active` flipping in the activation-invariant test,
 * which the test reverses at the end.
 */

// page.request is its own context — it hits whatever URL we point it at,
// not the page's origin. The frontend dev server (acme.localhost:7777)
// doesn't proxy /api, so direct API requests go to the backend.
const API_BASE = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

async function getAuthToken(page: import('@playwright/test').Page): Promise<string> {
	// signInAndWait drops the JWT into localStorage as auth_token.
	const token = await page.evaluate(() => localStorage.getItem('auth_token'));
	if (!token) throw new Error('not signed in');
	return token;
}

function apiHeaders(token: string) {
	return {
		Authorization: `Bearer ${token}`,
		'X-Tenant-Slug': 'acme'
	};
}

async function deleteWorkflowById(
	page: import('@playwright/test').Page,
	id: string
) {
	const token = await getAuthToken(page);
	await page.request.delete(`${API_BASE}/api/workflows/${id}`, {
		headers: apiHeaders(token)
	});
}

async function patchWorkflow(
	page: import('@playwright/test').Page,
	id: string,
	body: Record<string, unknown>
) {
	const token = await getAuthToken(page);
	return page.request.patch(`${API_BASE}/api/workflows/${id}`, {
		headers: apiHeaders(token),
		data: body
	});
}

async function listWorkflows(page: import('@playwright/test').Page) {
	const token = await getAuthToken(page);
	const resp = await page.request.get(`${API_BASE}/api/workflows`, {
		headers: apiHeaders(token)
	});
	return (await resp.json()) as Array<{
		id: string;
		is_default: boolean;
		is_active: boolean;
	}>;
}

test.describe('workflow lifecycle (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
	});

	test('create-from-modal redirects to the detail page and adds a list row', async ({
		page
	}) => {
		const name = `Test Workflow ${Date.now()}`;
		await page.goto('/workflows');
		await page.waitForLoadState('networkidle');
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
			await expect(page.locator('h2.page-title')).toContainText(name);

			// Newly-created workflows start `is_active=False` per the
			// API contract. Toggle button should reflect that.
			await expect(page.locator('button.btn-toggle')).toHaveText('Inactive');

			// Going back to the list shows the new row.
			await page.goto('/workflows');
			await page.waitForLoadState('networkidle');
			expect(await page.locator('table tbody tr').count()).toBe(beforeRows + 1);
			await expect(
				page.locator('table tbody tr', { hasText: name })
			).toBeVisible();
		} finally {
			await deleteWorkflowById(page, newId);
		}
	});

	test('default workflow cannot be deleted: API returns 409', async ({ page }) => {
		const list = await listWorkflows(page);
		const defaultWf = list.find((w) => w.is_default);
		expect(defaultWf).toBeTruthy();

		const token = await getAuthToken(page);
		const resp = await page.request.delete(
			`${API_BASE}/api/workflows/${defaultWf!.id}`,
			{ headers: apiHeaders(token) }
		);
		expect(resp.status()).toBe(409);
	});

	test('non-default workflow can be deleted via the list', async ({ page }) => {
		// Create a throwaway workflow, then delete via the list-row button.
		await page.goto('/workflows');
		await page.waitForLoadState('networkidle');
		const name = `Delete Me ${Date.now()}`;
		await page.getByRole('button', { name: '+ New Workflow' }).click();
		await page.locator('#wf-name').fill(name);
		await page.getByRole('button', { name: /^Create$/ }).click();
		await page.waitForURL(/\/workflows\/[a-f0-9-]{36}/);
		const newId = page.url().match(/\/workflows\/([a-f0-9-]{36})/)![1];

		// Bounce back to the list.
		await page.goto('/workflows');
		await page.waitForLoadState('networkidle');
		const row = page.locator('table tbody tr', { hasText: name });
		await expect(row).toBeVisible();

		const deleted = page.waitForResponse(
			(r) =>
				r.url().includes(`/api/workflows/${newId}`) &&
				r.request().method() === 'DELETE'
		);
		await row.locator('button.delete-btn').click();
		await deleted;
		await expect(row).toBeHidden({ timeout: 5_000 });
	});

	test('one-active invariant: activating a new workflow deactivates the seeded default', async ({
		page
	}) => {
		await page.goto('/workflows');
		await page.waitForLoadState('networkidle');
		const before = await listWorkflows(page);
		const defaultWf = before.find((w) => w.is_default)!;
		expect(defaultWf.is_active).toBe(true);

		const name = `Activate Me ${Date.now()}`;
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
