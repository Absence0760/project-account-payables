import { API_BASE, expect, tenantPsql, test } from '../fixtures/helpers';

/** Both tests provision a second entity to make the switcher appear; these are
 *  the slug prefixes they use, and what `afterEach` cleans up. */
const SLUG_PREFIXES = ['e2e-sub', 'e2e-esc'];

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
 *
 * The ENTITY ROWS did leak, though, and unboundedly: the slug carries a
 * timestamp so reruns never collide, and `/api/entities` has no DELETE, so two
 * subsidiaries accumulated in the shared `e2e<N>` tenant on every run. That is
 * not cosmetic — the switcher renders only above one entity, `/admin/entities`
 * pages the list, and `GET /analytics/by-entity` reports one row per entity, so
 * a later spec sees a tenant shaped by however many times this file has run.
 * `afterEach` removes them by slug prefix, the pattern
 * `entities/deactivated-entity.spec.ts` established. Nothing is filed under
 * these entities (both tests only read the dashboard, and the first returns to
 * the consolidated view before it ends), so the rows delete cleanly — and if a
 * future test does file something under one, the FK refuses and the teardown
 * fails loudly rather than half-cleaning.
 */

const API = API_BASE;

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
	test.afterEach(() => {
		const where = SLUG_PREFIXES.map((p) => `slug LIKE '${p}-%'`).join(' OR ');
		tenantPsql(`DELETE FROM entities WHERE ${where}`);
	});

	test('appears with >1 entity and scopes requests by selection', async ({ page }) => {
		// Unique slug per run so reruns don't collide on the slug constraint.
		const suffix = `${Date.now().toString(36)}`;
		const name = `E2E Sub ${suffix}`;
		const slug = `${SLUG_PREFIXES[0]}-${suffix}`;

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

	test('a deactivated entity drops out of the switcher options', async ({ page }) => {
		const suffix = `${Date.now().toString(36)}`;
		const name = `E2E Deact ${suffix}`;
		const slug = `e2e-deact-${suffix}`;

		await page.goto('/');
		await page.waitForLoadState('networkidle');
		const entityId = await createEntity(page, name, slug);
		await page.reload();
		await page.waitForLoadState('networkidle');

		// Visible while active.
		await page.locator('.entity-btn').click();
		await expect(page.locator('.entity-option', { hasText: name })).toBeVisible();
		await page.keyboard.press('Escape');

		// Deactivate it via the API, reload, and it must no longer be pickable —
		// a new row created while scoped to it would land under a dead entity.
		await page.evaluate(
			async ({ api, id }) => {
				const token = localStorage.getItem('auth_token');
				const tenant = window.location.hostname.split('.')[0];
				const res = await fetch(`${api}/api/entities/${id}`, {
					method: 'PATCH',
					headers: {
						'Content-Type': 'application/json',
						Authorization: `Bearer ${token}`,
						'X-Tenant-Slug': tenant
					},
					body: JSON.stringify({ is_active: false })
				});
				if (!res.ok) throw new Error(`deactivate failed: ${res.status}`);
			},
			{ api: API, id: entityId }
		);
		await page.reload();
		await page.waitForLoadState('networkidle');

		// The deactivated entity can no longer be picked. Two valid end states,
		// depending on how many OTHER entities the shared worker tenant carries
		// (other specs in this file leak them): the switcher hides entirely
		// (this was the only second entity), or it stays but drops the option.
		const switcher = page.locator('.entity-btn');
		if (await switcher.isVisible()) {
			await switcher.click();
			await expect(page.locator('.entity-menu')).toBeVisible();
			await expect(page.locator('.entity-option', { hasText: name })).toHaveCount(0);
		} else {
			await expect(switcher).toBeHidden();
		}
	});

	test('Escape closes the open menu and restores focus to the trigger', async ({ page }) => {
		// The menu only renders with >1 entity, so provision a second one first.
		const suffix = `${Date.now().toString(36)}`;
		const name = `E2E Esc ${suffix}`;
		const slug = `${SLUG_PREFIXES[1]}-${suffix}`;

		await page.goto('/');
		await page.waitForLoadState('networkidle');
		await createEntity(page, name, slug);
		await page.reload();
		await page.waitForLoadState('networkidle');

		await page.locator('.entity-btn').click();
		await expect(page.locator('.entity-menu')).toBeVisible();

		// Old behaviour: backdrop `onkeydown` was a no-op, so Escape did nothing
		// and the menu stayed open with no keyboard way out.
		await page.keyboard.press('Escape');
		await expect(page.locator('.entity-menu')).toBeHidden();
		await expect(page.locator('.entity-btn')).toBeFocused();
	});
});
