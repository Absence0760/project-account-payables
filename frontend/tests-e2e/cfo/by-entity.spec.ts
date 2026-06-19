import { expect, test } from '../fixtures/helpers';

/**
 * /cfo — "By entity" consolidated-reporting section.
 *
 * The section (consolidated reporting ACROSS entities) self-hides for
 * single-entity tenants, mirroring the sidebar entity switcher. The seed
 * tenants ship with one Default entity, so this spec creates a second entity
 * via the API, then asserts the By-entity table renders on /cfo with the
 * consolidated cross-check row.
 *
 * Default storage state signs the worker's admin in, so admin reaches the
 * CFO surface (gated admin + cfo).
 */

const API = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

async function createEntity(page, name: string, slug: string): Promise<string> {
	return page.evaluate(
		async ({ api, name, slug }) => {
			const token = localStorage.getItem('auth_token');
			const tenant = window.location.hostname.split('.')[0];
			const res = await fetch(`${api}/api/entities`, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					Authorization: `Bearer ${token}`,
					'X-Tenant-Slug': tenant
				},
				body: JSON.stringify({ name, slug })
			});
			if (!res.ok) throw new Error(`create entity failed: ${res.status}`);
			return (await res.json()).id as string;
		},
		{ api: API, name, slug }
	);
}

test.describe('/cfo By-entity section', () => {
	test('renders the per-entity breakdown for a multi-entity tenant', async ({ page }) => {
		const suffix = `${Date.now().toString(36)}`;
		const name = `CFO Sub ${suffix}`;
		const slug = `cfo-sub-${suffix}`;

		await page.goto('/cfo');
		await page.waitForLoadState('networkidle');
		await createEntity(page, name, slug);

		// Reload so the entity store picks up the new entity and the section
		// stops self-hiding.
		const byEntityResp = page.waitForResponse((r) =>
			r.url().includes('/api/analytics/by-entity')
		);
		await page.reload();
		await byEntityResp;
		await page.waitForLoadState('networkidle');

		const section = page.getByTestId('by-entity-section');
		await expect(section).toBeVisible();
		await expect(section.getByRole('heading', { name: 'By entity' })).toBeVisible();
		// The new entity is one row; the consolidated cross-check is the total row.
		await expect(section.locator('tr', { hasText: name })).toBeVisible();
		await expect(section.locator('tr.be-total', { hasText: 'Consolidated' })).toBeVisible();
	});
});
