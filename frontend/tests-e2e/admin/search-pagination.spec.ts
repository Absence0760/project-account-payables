import { execFileSync } from 'node:child_process';

import {
	API_BASE,
	authedTenantHeaders,
	currentTenantSlug,
	expect,
	test
} from '../fixtures/helpers';

async function createUser(
	page: import('@playwright/test').Page,
	email: string,
	fullName: string
): Promise<string> {
	const resp = await page.request.post(`${API_BASE}/api/admin/users`, {
		headers: await authedTenantHeaders(page),
		data: { full_name: fullName, email, role_names: [] }
	});
	return ((await resp.json()) as { id: string }).id;
}

async function deleteUser(page: import('@playwright/test').Page, id: string) {
	await page.request.delete(`${API_BASE}/api/admin/users/${id}`, {
		headers: await authedTenantHeaders(page)
	});
}

/**
 * Bulk-clean any e2e-created users via direct SQL against the control
 * plane. Users live in the control-plane DB (not per-tenant), so we
 * scope the purge by email suffix to the current worker's tenant slug
 * to avoid stomping on other workers' rows.
 */
function purgeE2EUsers(): void {
	const slug = currentTenantSlug();
	const pattern = `e2e-search-%@${slug}.test.local`;
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
			'feohledger',
			'-c',
			`DELETE FROM user_roles WHERE user_id IN (SELECT id FROM users WHERE email LIKE '${pattern}')`,
			'-c',
			`DELETE FROM users WHERE email LIKE '${pattern}'`
		],
		{ env: { ...process.env, PGPASSWORD: 'postgres' }, stdio: 'pipe' }
	);
}

/**
 * /admin user list — search + pagination. The list endpoint defaults
 * to page_size=20, supports ?search= against full_name + email
 * (case-insensitive), and the UI exposes both a debounced search
 * input and a "Load more" button.
 */

test.describe('/admin user search + pagination', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/admin');
		await page.waitForLoadState('networkidle');
	});

	test.afterEach(() => {
		purgeE2EUsers();
	});

	test('search input is rendered and filters by name (case-insensitive)', async ({
		page,
		tenantSlug
	}) => {
		const created: string[] = [];
		try {
			const ts = Date.now();
			created.push(
				await createUser(
					page,
					`e2e-search-alpha-${ts}@${tenantSlug}.test.local`,
					'Alpha Centauri'
				),
				await createUser(
					page,
					`e2e-search-beta-${ts}@${tenantSlug}.test.local`,
					'Beta Pictoris'
				)
			);

			await page.reload();
			await page.waitForLoadState('networkidle');

			const searchInput = page.getByPlaceholder('Search name or email...');
			await expect(searchInput).toBeVisible();

			// Search by partial name — case-insensitive ILIKE on the backend.
			const filtered = page.waitForResponse(
				(r) =>
					r.url().includes('/api/admin/users') &&
					r.url().includes('search=alpha')
			);
			await searchInput.fill('alpha');
			await filtered;

			// Only the alpha row remains.
			await expect(
				page.locator('table tbody tr', { hasText: 'Alpha Centauri' })
			).toBeVisible();
			await expect(
				page.locator('table tbody tr', { hasText: 'Beta Pictoris' })
			).toHaveCount(0);
		} finally {
			for (const id of created) await deleteUser(page, id);
		}
	});

	test('search by email substring also matches', async ({ page, tenantSlug }) => {
		const created: string[] = [];
		try {
			const ts = Date.now();
			created.push(
				await createUser(
					page,
					`e2e-search-needle-${ts}@${tenantSlug}.test.local`,
					'Findable User'
				)
			);

			await page.reload();
			await page.waitForLoadState('networkidle');

			const filtered = page.waitForResponse(
				(r) =>
					r.url().includes('/api/admin/users') &&
					r.url().includes('search=needle')
			);
			await page.getByPlaceholder('Search name or email...').fill('needle');
			await filtered;
			await expect(
				page.locator('table tbody tr', { hasText: 'Findable User' })
			).toBeVisible();
		} finally {
			for (const id of created) await deleteUser(page, id);
		}
	});

	test('clearing search restores the unfiltered list', async ({ page, tenantSlug }) => {
		const created: string[] = [];
		try {
			const ts = Date.now();
			created.push(
				await createUser(
					page,
					`e2e-search-clear-${ts}@${tenantSlug}.test.local`,
					'Clear Test'
				)
			);
			await page.reload();
			await page.waitForLoadState('networkidle');

			const baseCount = await page.locator('table tbody tr').count();

			await page.getByPlaceholder('Search name or email...').fill('Clear Test');
			await page.waitForResponse(
				(r) => r.url().includes('/api/admin/users') && r.url().includes('search=')
			);
			expect(await page.locator('table tbody tr').count()).toBeLessThan(baseCount);

			// Empty the input — the debounced effect re-fires the request
			// without ?search.
			await page.getByPlaceholder('Search name or email...').fill('');
			await page.waitForResponse(
				(r) =>
					r.url().includes('/api/admin/users') &&
					!r.url().includes('search=')
			);
			await expect(page.locator('table tbody tr')).toHaveCount(baseCount);
		} finally {
			for (const id of created) await deleteUser(page, id);
		}
	});

	test('pagination: large user count surfaces a Load more button that appends', async ({
		page,
		tenantSlug
	}) => {
		// Create 22 throwaway users to push the list past page_size=20.
		// All start with "e2e-search-page-" so afterEach can sweep them.
		const created: string[] = [];
		const ts = Date.now();
		try {
			for (let i = 0; i < 22; i++) {
				created.push(
					await createUser(
						page,
						`e2e-search-page-${ts}-${i}@${tenantSlug}.test.local`,
						`Pageable User ${i}`
					)
				);
			}

			// Filter to just our throwaway batch so the test is independent
			// of how many users the seed has.
			await page.reload();
			await page.waitForLoadState('networkidle');
			await page.getByPlaceholder('Search name or email...').fill(`e2e-search-page-${ts}`);
			await page.waitForResponse(
				(r) => r.url().includes('/api/admin/users') && r.url().includes('search=')
			);

			// First page renders 20 rows; Load more is visible.
			await expect(page.locator('table tbody tr')).toHaveCount(20);
			const loadMore = page.getByRole('button', { name: /Load more/ });
			await expect(loadMore).toBeVisible();
			await expect(loadMore).toContainText('20 of 22');

			// Click Load more — appends, total becomes 22.
			const next = page.waitForResponse(
				(r) =>
					r.url().includes('/api/admin/users') &&
					r.url().includes('page=2')
			);
			await loadMore.click();
			await next;

			await expect(page.locator('table tbody tr')).toHaveCount(22);
			await expect(loadMore).toHaveCount(0);
			await expect(page.locator('.load-more-end')).toContainText(/Showing all 22/);
		} finally {
			for (const id of created) await deleteUser(page, id);
		}
	});
});
