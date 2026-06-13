import { expect, test } from '../fixtures/helpers';

/**
 * Multi-entity (Phase 2) sidebar entity switcher.
 *
 * The seed tenants ship with a single Default entity, so the switcher is
 * hidden. This spec creates a second entity via the API, then drives the UI:
 * the switcher appears, selecting an entity scopes requests (X-Entity-ID
 * header), and "All entities" returns to the consolidated view (no header).
 *
 * Selection persists in tenant-scoped localStorage, but each test loads from
 * the worker's storageState snapshot (which carries no selection), so this
 * doesn't leak into other specs.
 */

const API = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

/** Create an entity through the backend API using the page's stored auth. */
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

test.describe('sidebar entity switcher', () => {
	test('appears with >1 entity and scopes requests by selection', async ({ page }) => {
		// Unique slug per run so reruns don't collide on the slug constraint.
		const suffix = `${Date.now().toString(36)}`;
		const name = `E2E Sub ${suffix}`;
		const slug = `e2e-sub-${suffix}`;

		await page.goto('/');
		await page.waitForLoadState('networkidle');
		const entityId = await createEntity(page, name, slug);

		// Reload so the switcher store picks up the new entity.
		await page.reload();
		await page.waitForLoadState('networkidle');

		const switcher = page.locator('.entity-btn');
		await expect(switcher).toBeVisible();

		// Selecting the new entity reloads and re-fetches with X-Entity-ID.
		const scopedReq = page.waitForRequest((r) => r.url().includes('/api/dashboard'));
		await switcher.click();
		await page.locator('.entity-option', { hasText: name }).click();
		const req = await scopedReq;
		expect(req.headers()['x-entity-id']).toBe(entityId);

		// After reload the switcher shows the selected entity name.
		await page.waitForLoadState('networkidle');
		await expect(page.locator('.entity-name')).toHaveText(name);

		// "All entities" returns to the consolidated view — no X-Entity-ID.
		const consolidatedReq = page.waitForRequest((r) => r.url().includes('/api/dashboard'));
		await page.locator('.entity-btn').click();
		await page.locator('.entity-option', { hasText: /^All entities/ }).click();
		const req2 = await consolidatedReq;
		expect(req2.headers()['x-entity-id']).toBeFalsy();

		await page.waitForLoadState('networkidle');
		await expect(page.locator('.entity-name')).toHaveText('All entities');
	});
});
