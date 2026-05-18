import { execFileSync } from 'node:child_process';

import { expect, test } from '../fixtures/helpers';

import { signInAndWait } from '../fixtures/helpers';

const API_BASE = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

async function authToken(page: import('@playwright/test').Page) {
	const t = await page.evaluate(() => localStorage.getItem('auth_token'));
	if (!t) throw new Error('not signed in');
	return t;
}

interface ExceptionRow {
	id: string;
	invoice_id: string;
	exception_type: string;
	status: string;
}

async function listExceptions(
	page: import('@playwright/test').Page,
	status: string
): Promise<ExceptionRow[]> {
	const token = await authToken(page);
	const resp = await page.request.get(`${API_BASE}/api/exceptions?status=${status}`, {
		headers: { Authorization: `Bearer ${token}`, 'X-Tenant-Slug': 'acme' }
	});
	const body = (await resp.json()) as { items: ExceptionRow[] };
	return body.items;
}

/**
 * Reset an exception back to `open` state in the ap_acme tenant DB.
 * The product API has no "reopen" endpoint by design (resolved
 * exceptions are immutable for audit purposes), so direct SQL is the
 * only revertible path. psql is available on dev workstations and in
 * the CI backend container.
 */
function resetExceptionToOpen(id: string): void {
	execFileSync(
		'psql',
		[
			'-h',
			'localhost',
			'-U',
			'postgres',
			'-p',
			'5432',
			'-d',
			'ap_acme',
			'-c',
			`UPDATE exceptions SET status='open', resolution=NULL, resolved_by=NULL, resolved_at=NULL WHERE id='${id}'`
		],
		{ env: { ...process.env, PGPASSWORD: 'postgres' }, stdio: 'pipe' }
	);
}

/**
 * /exceptions — resolve / escalate / dismiss UI flows. Each test
 * mutates state then reverts it via psql in finally so the suite is
 * re-runnable. Open exceptions come from the seed (3 per tenant).
 */

test.describe('/exceptions resolve actions (acme admin)', () => {
	test.beforeEach(async ({ page }) => {
		await signInAndWait(page);
		await page.goto('/exceptions');
		await page.waitForLoadState('networkidle');
	});

	test('Resolve button opens the resolve modal with three action choices', async ({ page }) => {
		const open = await listExceptions(page, 'open');
		expect(open.length).toBeGreaterThan(0);

		const row = page.locator('table tbody tr').first();
		await row.getByRole('button', { name: 'Resolve' }).click();

		const modal = page.getByRole('dialog', { name: 'Resolve exception' });
		await expect(modal).toBeVisible();
		await expect(modal.getByRole('button', { name: 'Resolve', exact: true })).toBeVisible();
		await expect(modal.getByRole('button', { name: 'Escalate' })).toBeVisible();
		await expect(modal.getByRole('button', { name: 'Dismiss' })).toBeVisible();

		// Cancel closes the modal.
		await modal.getByRole('button', { name: 'Cancel' }).click();
		await expect(modal).toBeHidden();
	});

	test('Resolve flips an open exception to resolved status', async ({ page }) => {
		const open = await listExceptions(page, 'open');
		expect(open.length).toBeGreaterThan(0);
		const target = open[0];

		try {
			const row = page.locator('table tbody tr').first();
			await row.getByRole('button', { name: 'Resolve' }).click();

			const modal = page.getByRole('dialog', { name: 'Resolve exception' });
			await modal.locator('input[type="text"]').fill('e2e: confirmed and closed');

			const posted = page.waitForResponse(
				(r) =>
					r.url().includes('/api/exceptions/') &&
					r.url().includes('/resolve') &&
					r.request().method() === 'POST'
			);
			await modal.getByRole('button', { name: 'Resolve', exact: true }).click();
			const resp = await posted;
			expect(resp.status()).toBe(200);
			expect(((await resp.json()) as { status: string }).status).toBe('resolved');
		} finally {
			resetExceptionToOpen(target.id);
		}
	});

	test('Escalate flips an open exception to escalated status', async ({ page }) => {
		const open = await listExceptions(page, 'open');
		expect(open.length).toBeGreaterThan(0);
		const target = open[0];

		try {
			const row = page.locator('table tbody tr').first();
			await row.getByRole('button', { name: 'Resolve' }).click();

			const modal = page.getByRole('dialog', { name: 'Resolve exception' });
			await modal.locator('input[type="text"]').fill('e2e: needs CFO review');

			const posted = page.waitForResponse(
				(r) =>
					r.url().includes('/api/exceptions/') &&
					r.url().includes('/resolve') &&
					r.request().method() === 'POST'
			);
			await modal.getByRole('button', { name: 'Escalate' }).click();
			const resp = await posted;
			expect(resp.status()).toBe(200);
			expect(((await resp.json()) as { status: string }).status).toBe('escalated');
		} finally {
			resetExceptionToOpen(target.id);
		}
	});

	test('Dismiss flips an open exception to dismissed status (no note required)', async ({
		page
	}) => {
		const open = await listExceptions(page, 'open');
		expect(open.length).toBeGreaterThan(0);
		const target = open[0];

		try {
			const row = page.locator('table tbody tr').first();
			await row.getByRole('button', { name: 'Resolve' }).click();

			const modal = page.getByRole('dialog', { name: 'Resolve exception' });
			// Dismiss accepts an empty note — sends "dismissd by user" server-side.
			const posted = page.waitForResponse(
				(r) =>
					r.url().includes('/api/exceptions/') &&
					r.url().includes('/resolve') &&
					r.request().method() === 'POST'
			);
			await modal.getByRole('button', { name: 'Dismiss' }).click();
			const resp = await posted;
			expect(resp.status()).toBe(200);
			expect(((await resp.json()) as { status: string }).status).toBe('dismissed');
		} finally {
			resetExceptionToOpen(target.id);
		}
	});
});
