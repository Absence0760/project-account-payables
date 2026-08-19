import { expect, test } from '../fixtures/helpers';

/**
 * `/workflows` — the bulk selection must never outlive the rows it points at.
 *
 * The page kept `selectedIds` as a `Set<string>` and never pruned it. The store
 * edits the list in place (`remove` / `bulkRemove` / `create` / `restore`) and
 * replaces it wholesale on `fetch()`, so a selected definition could leave the
 * table — deleted from its own row action, or promoted to default — while its
 * id stayed selected. The floating bulk bar then counted rows nobody could see
 * and `bulkRemove` POSTed them, coming back with a `not_found` / `default`
 * failure whose cause was invisible.
 *
 * `/invoices`, `/exceptions`, `/expenses` and `/payments` all carry the
 * `pruneSelection` guard; this asserts `/workflows` now does too.
 *
 * The list is stubbed so the selection maths doesn't depend on what the shard's
 * tenant happens to hold.
 */

const WF_DEFAULT = '00000000-0000-4000-8200-000000000001';
const WF_A = '00000000-0000-4000-8200-000000000002';
const WF_B = '00000000-0000-4000-8200-000000000003';

function workflow(id: string, name: string, isDefault = false) {
	return {
		id,
		name,
		description: null,
		is_default: isDefault,
		is_active: !isDefault,
		steps_config: { steps: [{ number: 1, type: 'approval', name: 'Approval', enabled: true }] },
		created_at: '2026-01-01T00:00:00Z',
		updated_at: null
	};
}

test.describe('/workflows — stale selection', () => {
	test('deleting a selected row drops it from the bulk selection', async ({ page }) => {
		await page.route('**/api/workflows*', async (route) => {
			const url = new URL(route.request().url());
			if (url.pathname !== '/api/workflows') {
				await route.fallback();
				return;
			}
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					items: [
						workflow(WF_DEFAULT, 'E2E Default Flow', true),
						workflow(WF_A, 'E2E Flow A'),
						workflow(WF_B, 'E2E Flow B')
					],
					total: 3,
					page: 1,
					page_size: 20
				})
			});
		});

		// Registered after the list route so it is matched first for its own URL.
		await page.route(`**/api/workflows/${WF_B}`, async (route) => {
			if (route.request().method() !== 'DELETE') {
				await route.fallback();
				return;
			}
			await route.fulfill({ status: 204, body: '' });
		});

		await page.goto('/workflows');
		const rowA = page.getByRole('row').filter({ hasText: 'E2E Flow A' });
		const rowB = page.getByRole('row').filter({ hasText: 'E2E Flow B' });
		await expect(rowB).toBeVisible();

		await rowA.getByLabel('Select E2E Flow A').check();
		await rowB.getByLabel('Select E2E Flow B').check();

		const bulkBar = page.locator('.bulk-bar');
		await expect(bulkBar.locator('.bulk-count')).toHaveText('2 selected');
		await expect(bulkBar.getByRole('button', { name: 'Delete 2' })).toBeVisible();

		// Delete B from its own row action — the store removes it in place, with
		// no refetch, so nothing else can prune the selection.
		await rowB.getByRole('button', { name: 'Delete', exact: true }).click();
		await expect(rowB).toHaveCount(0);

		// The bar must now count only the row still on screen, and the button it
		// feeds must POST one id, not two.
		await expect(bulkBar.locator('.bulk-count')).toHaveText('1 selected');
		await expect(bulkBar.getByRole('button', { name: 'Delete 1' })).toBeVisible();
		await expect(bulkBar.getByRole('button', { name: 'Delete 2' })).toHaveCount(0);

		// And clearing the last one dismisses the bar entirely.
		await rowA.getByLabel('Select E2E Flow A').uncheck();
		await expect(bulkBar).toHaveCount(0);
	});
});
