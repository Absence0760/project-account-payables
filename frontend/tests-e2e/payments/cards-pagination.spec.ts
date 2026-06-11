import { API_BASE, authedTenantHeaders, expect, tenantPsql, test } from '../fixtures/helpers';

/**
 * /payments cards-tab pagination. The cards tab previously fetched every card;
 * it now uses the shared Load-More at page_size=20. We bulk-insert virtual
 * cards (against a seeded invoice) past the boundary and assert the contract.
 * Cards isn't the default tab (queue is), so the test switches to it first.
 *
 * Real card issuance needs an approved invoice + a card-adapter round trip, so
 * the fixture inserts rows directly — the list/pagination path under test
 * doesn't care how the cards were created, only that there are >page_size of
 * them.
 */

const MARKER = 'PAGE-CARD-';

function seedCards(n: number): void {
	tenantPsql(
		`INSERT INTO virtual_cards (id, invoice_id, organization_id, card_provider, provider_card_id, amount_limit, currency, status, last_four, created_at, updated_at)
		 SELECT gen_random_uuid(), i.id, i.organization_id, 'mock', '${MARKER}' || g, 500.00, 'USD', 'active', '4242', now(), now()
		 FROM generate_series(1, ${n}) g, (SELECT id, organization_id FROM invoices LIMIT 1) i`
	);
}

function purge(): void {
	tenantPsql(`DELETE FROM virtual_cards WHERE provider_card_id LIKE '${MARKER}%'`);
}

test.describe('/payments cards pagination', () => {
	test.afterEach(() => purge());

	test('cards tab Load more appends the next page', async ({ page }) => {
		seedCards(22);

		await page.goto('/payments');
		await page.waitForLoadState('networkidle');
		await page.getByRole('button', { name: 'Cards', exact: true }).click();
		await page.waitForResponse((r) => r.url().includes('/api/cards?') && r.url().includes('page=1'));

		const firstPageRows = await page.locator('table tbody tr').count();
		expect(firstPageRows).toBeLessThanOrEqual(20);

		const loadMore = page.getByRole('button', { name: /Load more/ });
		await expect(loadMore).toBeVisible();
		const total = Number((await loadMore.textContent())?.match(/of\s+(\d+)/)?.[1]);
		expect(total).toBeGreaterThanOrEqual(22);

		const next = page.waitForResponse(
			(r) => r.url().includes('/api/cards?') && r.url().includes('page=2')
		);
		await loadMore.click();
		await next;
		expect(await page.locator('table tbody tr').count()).toBeGreaterThan(firstPageRows);
	});

	test('API default page size is 20', async ({ page }) => {
		seedCards(25);
		const resp = await page.request.get(`${API_BASE}/api/cards`, {
			headers: await authedTenantHeaders(page)
		});
		const body = (await resp.json()) as { items: unknown[]; total: number; page_size: number };
		expect(body.page_size).toBe(20);
		expect(body.items.length).toBeLessThanOrEqual(20);
		expect(body.total).toBeGreaterThanOrEqual(25);
	});
});
