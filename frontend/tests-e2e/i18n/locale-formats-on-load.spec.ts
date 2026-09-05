import { API_BASE, authedTenantHeaders, deleteInvoicesWhere, expect, tenantPsql, test } from '../fixtures/helpers';

/**
 * A stored non-English locale must format MONEY AND DATES, not just labels.
 *
 * `i18n/formatLocale.ts` is a deliberately framework-free holder so pure
 * `money.ts` needn't import the Svelte runtime — which meant `formatMoney` /
 * `formatDate` had no dependency on it at all. `initLocale()` applied a stored
 * locale asynchronously (the catalogue is a lazy `import()`), so on a page load
 * with `feoh_locale=de` every `m()` label re-rendered in German while every
 * money cell and date stayed in the browser locale until that component
 * remounted. The result was a page reading half German, half en-US — the
 * mixed-locale figures this spec exists to catch.
 *
 * The assertion is on formatting SHAPE, not on an exact string: German uses
 * `.` for thousands and `,` for decimals and trails the currency symbol, so a
 * German-formatted amount can never contain an en-US `1,234.56`. Pinning the
 * exact glyphs would make this spec a hostage to the ICU data shipped with
 * whatever Chromium Playwright pulls.
 *
 * The other half of the fix — a MID-SESSION locale switch re-rendering already
 * mounted money — can't be reached from here: the picker lives on `/profile`,
 * no page carries both it and a money cell, and a client-side navigation
 * mounts the destination fresh (so it would pass even unfixed). That half is
 * locked by the source scan in `src/lib/i18n/formatLocale.test.ts`, which
 * asserts the `createSubscriber` wiring the reactivity depends on.
 *
 * The two shapes are only distinguishable on a FOUR-FIGURE amount, so this
 * spec creates its own and reads that row alone rather than trusting whatever
 * the tenant happens to hold. It used to read the seeded invoice list, which
 * passes on the full local seed (invoices of 1250–12000) and fails on CI's
 * `--lean` seed (`seed_tenant_lean` mints LEAN-001..015 at 101.00–115.00, none
 * of which can carry a thousands separator in EITHER locale). The guard below
 * caught that honestly — it is the assertion refusing to pass vacuously, not a
 * formatting regression — so the fix is to own the datum, not to soften it.
 */

/** en-US groups with `,` and points with `.` — `1,234.56`. */
const EN_US_AMOUNT = /\d,\d{3}\.\d{2}/;
/** de-DE groups with `.` and points with `,` — `1.234,56`. */
const DE_DE_AMOUNT = /\d\.\d{3},\d{2}/;

/** Four figures, so BOTH locales must render a thousands separator, and a
 *  distinct digit in every group so a mis-grouped render can't coincide. */
const FOUR_FIGURE_AMOUNT = '1234.56';

test.describe('stored locale formats money on first load', () => {
	let invoiceId: string | null = null;

	test.afterEach(() => {
		if (!invoiceId) return;
		tenantPsql(
			`DELETE FROM workflow_steps WHERE instance_id IN (SELECT id FROM workflow_instances WHERE invoice_id='${invoiceId}')`
		);
		tenantPsql(`DELETE FROM workflow_instances WHERE invoice_id='${invoiceId}'`);
		tenantPsql(`DELETE FROM exceptions WHERE invoice_id='${invoiceId}'`);
		deleteInvoicesWhere(`id='${invoiceId}'`);
		invoiceId = null;
	});

	test('a de locale renders German-formatted amounts, not en-US ones', async ({
		page,
		tenantSlug
	}) => {
		// Own the datum the assertion reads. A seeded tenant's amounts are not
		// this spec's to depend on — the lean CI seed's are all three-figure, and
		// a three-figure amount is byte-identical in de-DE and en-US.
		const invoiceNumber = `E2E-LOCALE-${Date.now()}`;
		const created = await page.request.post(`${API_BASE}/api/invoices`, {
			headers: await authedTenantHeaders(page),
			data: {
				vendor: 'E2E Locale Format Vendor',
				invoice_number: invoiceNumber,
				amount: FOUR_FIGURE_AMOUNT,
				currency: 'USD'
			}
		});
		expect(created.ok(), 'fixture invoice must be created').toBeTruthy();
		invoiceId = ((await created.json()) as { id: string }).id;

		// Seed the picker's own storage key before any app code runs, so this is
		// the same path a returning German user takes — not a mid-session switch.
		await page.addInitScript(() => {
			localStorage.setItem('feoh_locale', 'de');
		});

		await page.goto(`http://${tenantSlug}.localhost:7777/invoices`);

		// Narrow the list to the one invoice this spec minted, so the amounts read
		// below are exactly the one whose value we chose — not a page of whatever
		// else the tenant is carrying. Waiting on the search response is a real
		// signal, not a sleep.
		const listed = page.waitForResponse(
			(r) =>
				r.url().includes('/api/invoices?') &&
				r.url().includes(`search=${encodeURIComponent(invoiceNumber)}`) &&
				r.request().method() === 'GET'
		);
		// Select the search box by its shared `.search-box` contract class, NOT by
		// placeholder text — under `feoh_locale=de` that placeholder is
		// "Rechnungen suchen …", which is the whole point of this spec.
		await page.locator('.search-box input').first().fill(invoiceNumber);
		await listed;

		const row = page.locator('table tbody tr', { hasText: invoiceNumber }).first();
		await expect(row).toBeVisible();

		// Wait on a real signal: the rendered money cell. `<Money>` emits `.money`
		// on every currency value, so this resolves without a sleep.
		const amounts = row.locator('td .money');
		await expect(amounts.first()).toBeVisible();

		const rendered = (await amounts.allTextContents()).join(' | ');

		// The four-figure amount above guarantees a thousands separator in either
		// locale. If neither shape is present the cell did not render the value we
		// created at all, and the shape assertions below would pass vacuously —
		// so fail loudly here instead.
		expect(
			DE_DE_AMOUNT.test(rendered) || EN_US_AMOUNT.test(rendered),
			`no amount carried a thousands separator, so locale shape is untestable: ${rendered}`
		).toBe(true);

		expect(rendered).not.toMatch(EN_US_AMOUNT);
		expect(rendered).toMatch(DE_DE_AMOUNT);
	});
});
