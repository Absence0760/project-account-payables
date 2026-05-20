import {
	API_BASE,
	authedTenantHeaders,
	expect,
	signIn,
	signInAndWait,
	signOut,
	test
} from '../fixtures/helpers';

interface CreatedUser {
	id: string;
	email: string;
	temp_password: string;
}

/**
 * /change-password — strength hints, mismatch handling, and a real
 * round-trip via a freshly-created admin-side user. Each test creates
 * the user, drives the change-password form, and deletes the user in
 * finally. The seed never gets touched.
 */

async function createTestUser(
	page: import('@playwright/test').Page
): Promise<CreatedUser> {
	const email = `e2e-pw-${Date.now()}@test.local`;
	const resp = await page.request.post(`${API_BASE}/api/admin/users`, {
		headers: await authedTenantHeaders(page),
		data: { full_name: 'Password Test', email, role_names: ['ap_clerk'] }
	});
	const body = (await resp.json()) as {
		id: string;
		email: string;
		temporary_password: string;
	};
	return { id: body.id, email: body.email, temp_password: body.temporary_password };
}

async function deleteTestUser(
	page: import('@playwright/test').Page,
	id: string
) {
	// We may have logged out as the admin during the test; sign back in
	// with the worker's admin so our DELETE goes through with admin scope.
	if (!(await page.evaluate(() => localStorage.getItem('auth_token')))) {
		await signInAndWait(page);
	}
	await page.request.delete(`${API_BASE}/api/admin/users/${id}`, {
		headers: await authedTenantHeaders(page)
	});
}

test.describe('/change-password', () => {
	test('strength hints update as the user types', async ({ page }) => {
		await signInAndWait(page);
		const created = await createTestUser(page);

		try {
			await signOut(page);
			await signIn(page, { email: created.email, password: created.temp_password });
			// must_change_password=true sends the user to /change-password.
			await page.waitForURL(/\/change-password$/, { timeout: 10_000 });

			// Empty state: no hint is satisfied.
			const items = page.locator('ul.strength li');
			await expect(items).toHaveCount(4);
			for (let i = 0; i < 4; i++) {
				await expect(items.nth(i)).not.toHaveClass(/ok/);
			}

			// Type a strong password.
			const newPw = 'Test-Password-1234';
			await page.locator('input[autocomplete="new-password"]').first().fill(newPw);
			// All four hints become satisfied.
			for (let i = 0; i < 4; i++) {
				await expect(items.nth(i)).toHaveClass(/ok/);
			}
		} finally {
			await deleteTestUser(page, created.id);
		}
	});

	test('successful change clears must_change_password and lands on /', async ({ page }) => {
		await signInAndWait(page);
		const created = await createTestUser(page);

		try {
			await signOut(page);
			await signIn(page, { email: created.email, password: created.temp_password });
			await page.waitForURL(/\/change-password$/);

			const newPw = 'Test-Password-1234';
			await page.locator('input[autocomplete="current-password"]').fill(created.temp_password);
			const newPwInputs = page.locator('input[autocomplete="new-password"]');
			await newPwInputs.first().fill(newPw);
			await newPwInputs.last().fill(newPw);

			const changed = page.waitForResponse(
				(r) =>
					r.url().includes('/api/auth/change-password') &&
					r.request().method() === 'POST'
			);
			await page.getByRole('button', { name: 'Change password' }).click();
			const resp = await changed;
			expect(resp.status()).toBe(200);
			const body = (await resp.json()) as { must_change_password: boolean };
			expect(body.must_change_password).toBe(false);

			// Lands on the tenant root.
			await page.waitForURL(/^http:\/\/[^/]+:7777\/?$/, { timeout: 10_000 });

			// Verify new password actually works: sign out, sign back in,
			// must_change_password is now false so we land on / directly.
			await signOut(page);
			await signIn(page, { email: created.email, password: newPw });
			await page.waitForURL(/^http:\/\/[^/]+:7777\/?$/, { timeout: 10_000 });
		} finally {
			await deleteTestUser(page, created.id);
		}
	});

	test('Submit button stays disabled until both passwords match and are strong', async ({
		page
	}) => {
		await signInAndWait(page);
		const created = await createTestUser(page);

		try {
			await signOut(page);
			await signIn(page, { email: created.email, password: created.temp_password });
			await page.waitForURL(/\/change-password$/);

			const submit = page.getByRole('button', { name: 'Change password' });
			await expect(submit).toBeDisabled();

			const newPwInputs = page.locator('input[autocomplete="new-password"]');
			await newPwInputs.first().fill('weakpw');
			// Still disabled — fails strength.
			await expect(submit).toBeDisabled();

			await newPwInputs.first().fill('Test-Password-1234');
			// Disabled — confirm is empty.
			await expect(submit).toBeDisabled();

			await newPwInputs.last().fill('Test-Password-1234');
			await expect(submit).toBeEnabled();
		} finally {
			await deleteTestUser(page, created.id);
		}
	});
});
