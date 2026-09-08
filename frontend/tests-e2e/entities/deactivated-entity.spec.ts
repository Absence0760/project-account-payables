import type { Page } from '@playwright/test';
import { expect, test, tenantPsql } from '../fixtures/helpers';

/**
 * The sidebar entity switcher must not offer a DEACTIVATED entity.
 *
 * `/admin/entities` can archive a subsidiary, but nothing downstream honoured
 * it: the switcher listed `entityStore.entities` whole, so a retired entity
 * stayed one click away — and staying selected was worse than being
 * selectable. `tenant.py::get_entity_id` validates only that the id EXISTS, so
 * `X-Entity-ID` pointing at an inactive entity is accepted and
 * `get_write_entity_id` keeps filing every new invoice, vendor and payment
 * under the subsidiary the tenant just retired.
 *
 * The fix has two halves, and this spec pins both:
 *
 *  1. the menu lists ACTIVE entities only;
 *  2. a selection the tenant retires mid-use is dropped back to the
 *     consolidated view — persisted, so the very next request stops carrying
 *     the retired id — and the retired entity is shown once, disabled, so the
 *     user's choice does not simply vanish from the menu with no explanation.
 *
 * Consolidated is the fallback rather than the default entity because it is
 * the one selection that asserts nothing; new rows then land on the tenant's
 * default entity server-side, which is where an un-scoped row belongs.
 *
 * Entities are created through the API and removed in `afterEach`, so nothing
 * accumulates in the shared per-worker tenant. Nothing here is filed under
 * them, so the rows delete cleanly.
 */

const SLUG_PREFIX = 'e2e-retire';
const API = process.env.PUBLIC_API_URL ?? 'http://localhost:8000';

/** Create an entity through the backend API using the page's stored auth. */
async function createEntity(page: Page, name: string, slug: string): Promise<string> {
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

/** Archive an entity — what the `/admin/entities` "Active" checkbox does. */
async function deactivateEntity(page: Page, id: string): Promise<void> {
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
			if (!res.ok) throw new Error(`deactivate entity failed: ${res.status}`);
		},
		{ api: API, id }
	);
}

test.describe('entity switcher — a deactivated entity', () => {
	test.afterEach(() => {
		tenantPsql(`DELETE FROM entities WHERE slug LIKE '${SLUG_PREFIX}-%'`);
	});

	test('is dropped as the live selection and cannot be re-selected', async ({ page }) => {
		const suffix = Date.now().toString(36);
		const name = `E2E Retired ${suffix}`;
		const slug = `${SLUG_PREFIX}-live-${suffix}`;

		await page.goto('/');
		await page.waitForLoadState('networkidle');
		const entityId = await createEntity(page, name, slug);

		// Pick it up in the switcher and select it — the state a real user is in
		// when an admin archives the entity out from under them.
		await page.reload();
		await page.waitForLoadState('networkidle');
		const scopedReq = page.waitForRequest((r) => r.url().includes('/api/dashboard'));
		await page.locator('.entity-btn').click();
		await page.locator('.entity-option', { hasText: name }).click();
		expect((await scopedReq).headers()['x-entity-id']).toBe(entityId);
		await page.waitForLoadState('networkidle');
		await expect(page.locator('.entity-name')).toHaveText(name);

		// The admin archives it.
		await deactivateEntity(page, entityId);
		await page.reload();
		await page.waitForLoadState('networkidle');

		// The scope falls back to the consolidated view rather than sitting on a
		// retired subsidiary.
		await expect(page.locator('.entity-name')).toHaveText('All entities');

		await page.locator('.entity-btn').click();
		// It is not offered: no ENABLED option carries its name…
		await expect(
			page.locator('.entity-option:not([disabled])', { hasText: name })
		).toHaveCount(0);
		// …and it has not silently vanished either — it is listed once, marked
		// deactivated and unselectable, so the user can see what became of the
		// choice they made.
		const retired = page.getByTestId('entity-option-retired');
		await expect(retired).toBeVisible();
		await expect(retired).toContainText(name);
		// `entity.deactivated`, this row's own key — it used to borrow the
		// admin table's `admin.entities.statusInactive` ("Inactive"), which
		// names a lifecycle FLAG rather than the change that just happened.
		await expect(retired).toContainText('Deactivated');
		await expect(retired).toBeDisabled();
		await page.keyboard.press('Escape');

		// The point of the whole fix: the retired id is off the wire, so new
		// rows stop landing in the archived subsidiary (the backend files an
		// un-scoped write under the tenant's default entity).
		const nextReq = page.waitForRequest((r) => r.url().includes('/api/invoices'));
		await page.goto('/invoices');
		expect((await nextReq).headers()['x-entity-id']).toBeFalsy();
	});

	test('is absent from the menu when it was never the live selection', async ({ page }) => {
		const suffix = Date.now().toString(36);
		const name = `E2E Archived ${suffix}`;
		const slug = `${SLUG_PREFIX}-cold-${suffix}`;

		await page.goto('/');
		await page.waitForLoadState('networkidle');
		const entityId = await createEntity(page, name, slug);
		await deactivateEntity(page, entityId);

		await page.reload();
		await page.waitForLoadState('networkidle');

		// The switcher still renders — the tenant HAS more than one entity, and
		// the consolidated view is the only way to read the archived one's
		// history — but the retired entity is not among the choices, in any
		// form: nothing was dropped, so there is nothing to explain.
		await page.locator('.entity-btn').click();
		await expect(page.locator('.entity-menu')).toBeVisible();
		await expect(page.locator('.entity-option', { hasText: name })).toHaveCount(0);
		await expect(page.getByTestId('entity-option-retired')).toHaveCount(0);
	});
});
