import { API_BASE, authedTenantHeaders, expect, test } from '../fixtures/helpers';

interface OrgResponse {
	id: string;
	name: string;
	slug: string;
	plan: string;
	settings: Record<string, unknown> & {
		company?: {
			tax_id?: string;
			address?: string;
			phone?: string;
			website?: string;
			logo_url?: string;
		};
		invoice_defaults?: {
			currency?: string;
			payment_terms?: string;
			number_prefix?: string;
			default_gl_account?: string;
			default_cost_center?: string;
		};
	};
}

async function getOrg(page: import('@playwright/test').Page): Promise<OrgResponse> {
	const resp = await page.request.get(`${API_BASE}/api/organization`, {
		headers: await authedTenantHeaders(page)
	});
	return (await resp.json()) as OrgResponse;
}

async function patchOrg(
	page: import('@playwright/test').Page,
	body: Record<string, unknown>
): Promise<void> {
	await page.request.patch(`${API_BASE}/api/organization`, {
		headers: await authedTenantHeaders(page),
		data: body
	});
}

/**
 * /organization — settings page. The page is one big set of sections
 * (Company Profile, Invoice Defaults, AI Extraction, ERP, Payments,
 * Cards, Security, Data Sync, Plan), each with its own Save button.
 *
 * We assert all sections render, and round-trip an edit via two
 * sections: Company Profile (sends name + settings.company) and
 * Invoice Defaults (sends settings.invoice_defaults). Both revert via
 * a PATCH in finally.
 */

test.describe('/organization settings', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/organization');
		await page.waitForLoadState('networkidle');
	});

	test('all section cards render with their headings', async ({ page }) => {
		const expected = [
			'Company Profile',
			'Invoice Defaults',
			'AI Extraction',
			'ERP Integration',
			'Payments (ACH / Wire / RTP)',
			'Virtual Cards',
			'Security',
			'Data Sync'
		];
		for (const heading of expected) {
			await expect(page.getByRole('heading', { name: heading })).toBeVisible();
		}
	});

	test('Company Profile saves a phone change and round-trips through GET', async ({
		page
	}) => {
		const before = await getOrg(page);
		const originalPhone = before.settings.company?.phone ?? '';
		const next = `+1-555-e2e-${Date.now() % 100000}`;

		try {
			const profileCard = page.locator('section.card', {
				has: page.getByRole('heading', { name: 'Company Profile' })
			});
			const phoneInput = profileCard.locator('input[type="tel"]');
			await phoneInput.fill(next);

			const saved = page.waitForResponse(
				(r) =>
					r.url().endsWith('/api/organization') &&
					r.request().method() === 'PATCH' &&
					r.status() === 200
			);
			await profileCard.getByRole('button', { name: /Save Profile/ }).click();
			const resp = await saved;
			expect(resp.status()).toBe(200);

			const after = await getOrg(page);
			expect(after.settings.company?.phone).toBe(next);
		} finally {
			await patchOrg(page, {
				settings: {
					company: {
						...(before.settings.company ?? {}),
						phone: originalPhone
					}
				}
			});
		}
	});

	test('Invoice Defaults saves a currency change and round-trips', async ({ page }) => {
		const before = await getOrg(page);
		const originalCurrency = before.settings.invoice_defaults?.currency ?? 'USD';
		// Pick a non-current currency so we know the value flipped.
		const next = originalCurrency === 'EUR' ? 'GBP' : 'EUR';

		try {
			const defaultsCard = page.locator('section.card', {
				has: page.getByRole('heading', { name: 'Invoice Defaults' })
			});
			await defaultsCard.locator('select').first().selectOption(next);

			const saved = page.waitForResponse(
				(r) =>
					r.url().endsWith('/api/organization') &&
					r.request().method() === 'PATCH' &&
					r.status() === 200
			);
			await defaultsCard.getByRole('button', { name: /Save Defaults/ }).click();
			await saved;

			const after = await getOrg(page);
			expect(after.settings.invoice_defaults?.currency).toBe(next);
		} finally {
			await patchOrg(page, {
				settings: {
					invoice_defaults: {
						...(before.settings.invoice_defaults ?? {}),
						currency: originalCurrency
					}
				}
			});
		}
	});

	test('Company Profile saves an address change and pre-fills it on reload', async ({
		page
	}) => {
		const before = await getOrg(page);
		const originalAddress = before.settings.company?.address ?? '';
		const next = `e2e address ${Date.now()}`;

		try {
			const profileCard = page.locator('section.card', {
				has: page.getByRole('heading', { name: 'Company Profile' })
			});
			await profileCard.locator('textarea').fill(next);

			const saved = page.waitForResponse(
				(r) =>
					r.url().endsWith('/api/organization') &&
					r.request().method() === 'PATCH' &&
					r.status() === 200
			);
			await profileCard.getByRole('button', { name: /Save Profile/ }).click();
			await saved;

			// Reload — page hydrates from /api/organization, so the textarea
			// should be repopulated with the new value.
			await page.reload();
			await page.waitForLoadState('networkidle');
			await expect(
				page
					.locator('section.card', {
						has: page.getByRole('heading', { name: 'Company Profile' })
					})
					.locator('textarea')
			).toHaveValue(next);
		} finally {
			await patchOrg(page, {
				settings: {
					company: {
						...(before.settings.company ?? {}),
						address: originalAddress
					}
				}
			});
		}
	});

	test('Security warns when "require MFA" is saved but the platform switch is off', async ({
		page
	}) => {
		// Local/CI dev always runs with FEOH_MFA_ENABLED=false, so saving
		// required=true here always lands on the "not enforced yet" branch —
		// see `settings.mfa.enforcement_active` in
		// backend/app/api/organization.py::_org_response.
		try {
			const securityCard = page.locator('section.card', {
				has: page.getByRole('heading', { name: 'Security' })
			});
			const checkbox = securityCard.locator('label.switch-row input[type="checkbox"]');
			await checkbox.check();

			const saved = page.waitForResponse(
				(r) =>
					r.url().endsWith('/api/organization') &&
					r.request().method() === 'PATCH' &&
					r.status() === 200
			);
			await securityCard.getByRole('button', { name: /Save/ }).click();
			await saved;

			await expect(page.getByTestId('mfa-enforcement-inactive')).toBeVisible();

			// The warning is derived from the PATCH response, not just the
			// initial load — reload and confirm it still renders from a fresh
			// GET too.
			await page.reload();
			await page.waitForLoadState('networkidle');
			await expect(page.getByTestId('mfa-enforcement-inactive')).toBeVisible();
		} finally {
			await patchOrg(page, { settings: { mfa: { required: false } } });
		}
	});
});
