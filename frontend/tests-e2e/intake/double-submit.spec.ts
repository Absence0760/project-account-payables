import { API_BASE, authedTenantHeaders, expect, test } from '../fixtures/helpers';

/**
 * Intake lifecycle actions must guard against a double-submit.
 *
 * Each handler sets `busyId = i.id` for the duration of its API call, but the
 * row-action buttons (Submit / Approve / Convert / Cancel) originally never
 * wired that to `disabled=`, so a fast double-click could fire the same
 * mutation twice — most damagingly Convert, which could create two
 * requisitions. The sibling /requisitions page already disables its in-flight
 * actions; intake now does too.
 *
 * This spec locks the fix at the load-bearing point: while a Submit request is
 * in flight, the button is disabled, so a second click can't reach the API.
 */

async function createIntake(page: import('@playwright/test').Page) {
	const request_number = `IN-DS-${Date.now()}`;
	const resp = await page.request.post(`${API_BASE}/api/intake`, {
		headers: await authedTenantHeaders(page),
		data: { request_number, title: 'Double-submit guard target', currency: 'USD' }
	});
	expect(resp.status()).toBe(201);
	const body = (await resp.json()) as { id: string; request_number: string };
	return body;
}

async function deleteIntake(page: import('@playwright/test').Page, id: string) {
	await page.request
		.delete(`${API_BASE}/api/intake/${id}`, { headers: await authedTenantHeaders(page) })
		.catch(() => {});
}

test.describe('/intake double-submit guard', () => {
	test('the Submit action is disabled while its request is in flight', async ({ page }) => {
		const intake = await createIntake(page);
		try {
			// Hold the submit request open so we can observe the in-flight state.
			let release: () => void = () => {};
			const gate = new Promise<void>((r) => (release = r));
			await page.route(`**/api/intake/${intake.id}/submit`, async (route) => {
				await gate;
				await route.continue();
			});

			await page.goto('/intake');

			const row = page.locator('tr', { hasText: intake.request_number });
			await expect(row).toBeVisible();

			const submit = row.getByRole('button', { name: 'Submit' });
			await expect(submit).toBeEnabled();
			await submit.click();

			// The fix: while the mutation is in flight the button is disabled, so a
			// second click is impossible. Before the fix it stayed enabled.
			await expect(submit).toBeDisabled();

			// Let the request complete; the row transitions to in_review and the
			// Submit action is no longer offered.
			release();
			await expect(row.getByRole('button', { name: 'Submit' })).toHaveCount(0);
		} finally {
			await deleteIntake(page, intake.id);
		}
	});
});
