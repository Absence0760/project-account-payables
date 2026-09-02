import { API_BASE, authedTenantHeaders, expect, test } from '../fixtures/helpers';

/**
 * /vendors — create a vendor and invite it to the supplier portal from the UI.
 *
 * issue #328 (persona-new-user): `POST /api/vendors` and
 * `POST /api/vendors/{id}/portal-users` both existed on the backend but the
 * page exposed neither — a new tenant couldn't onboard a supplier by clicking
 * anything. Now: a "+ New Vendor" header action opens `CreateVendorModal`, and
 * an "Invite" row action opens `InviteVendorPortalUserModal` whose one-time
 * temp password is shown through the shared `SecretReveal`.
 *
 * The vendor is created fresh (unique name), then deleted via API in `finally`
 * — a never-transacted vendor is hard-deletable and cascades its portal user.
 */

test.describe('/vendors create + portal invite (acme admin)', () => {
	test('create a vendor, then invite it to the portal', async ({ page }) => {
		const stamp = Date.now();
		const vendorName = `E2E New Vendor ${stamp}`;
		const contactEmail = `e2e-portal-${stamp}@example.test`;
		let vendorId = '';

		try {
			await page.goto('/vendors');
			await page.waitForLoadState('networkidle');

			// --- Create ---
			await page.getByRole('button', { name: 'New Vendor' }).click();
			const createModal = page.getByRole('dialog', { name: 'Create vendor' });
			await expect(createModal).toBeVisible();
			// Labels carry a trailing " *" for required fields, so match non-exact.
			await createModal.getByLabel('Name').fill(vendorName);
			await createModal.getByLabel('Code').fill(`E2E-${stamp}`);

			const createResp = page.waitForResponse(
				(r) => r.url().endsWith('/api/vendors') && r.request().method() === 'POST'
			);
			await createModal.getByRole('button', { name: 'Create vendor' }).click();
			vendorId = (await (await createResp).json()).id as string;
			expect(vendorId).toBeTruthy();

			// The new vendor shows up in the list.
			const row = page.locator('table tbody tr', { hasText: vendorName });
			await expect(row).toHaveCount(1, { timeout: 10_000 });

			// --- Invite to portal ---
			await row.getByRole('button', { name: 'Invite' }).click();
			const inviteModal = page.getByRole('dialog', {
				name: 'Invite vendor to the supplier portal'
			});
			await expect(inviteModal).toBeVisible();
			await inviteModal.getByLabel('Contact name').fill('E2E Portal Contact');
			await inviteModal.getByLabel('Email').fill(contactEmail);

			const inviteResp = page.waitForResponse(
				(r) =>
					r.url().includes(`/api/vendors/${vendorId}/portal-users`) &&
					r.request().method() === 'POST'
			);
			await inviteModal.getByRole('button', { name: 'Send invite' }).click();
			expect((await inviteResp).status()).toBe(201);

			// The one-time temp password is revealed and non-empty.
			const secret = page.getByTestId('vendor-invite-temp-password');
			await expect(secret).toBeVisible({ timeout: 10_000 });
			expect((await secret.textContent())?.trim().length ?? 0).toBeGreaterThan(8);

			// Dismiss — the secret must be gone from the DOM afterwards.
			await page.getByRole('button', { name: 'Done' }).click();
			await expect(secret).toHaveCount(0);
		} finally {
			if (vendorId) {
				await page.request.delete(`${API_BASE}/api/vendors/${vendorId}`, {
					headers: await authedTenantHeaders(page)
				});
			}
		}
	});
});
