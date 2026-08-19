import { expect, test } from '../fixtures/helpers';

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
 */

/** en-US groups with `,` and points with `.` — `1,234.56`. */
const EN_US_AMOUNT = /\d,\d{3}\.\d{2}/;
/** de-DE groups with `.` and points with `,` — `1.234,56`. */
const DE_DE_AMOUNT = /\d\.\d{3},\d{2}/;

test.describe('stored locale formats money on first load', () => {
	test('a de locale renders German-formatted amounts, not en-US ones', async ({
		page,
		tenantSlug
	}) => {
		// Seed the picker's own storage key before any app code runs, so this is
		// the same path a returning German user takes — not a mid-session switch.
		await page.addInitScript(() => {
			localStorage.setItem('feoh_locale', 'de');
		});

		await page.goto(`http://${tenantSlug}.localhost:7777/invoices`);

		// Wait on a real signal: the first rendered money cell. `<Money>` emits
		// `.money` on every currency value, and the seeded tenant always has
		// invoices, so this resolves without a sleep.
		const amounts = page.locator('td .money');
		await expect(amounts.first()).toBeVisible();

		const rendered = (await amounts.allTextContents()).join(' | ');

		// At least one amount must be large enough to show a thousands
		// separator, otherwise the two shapes are indistinguishable and the
		// assertion below would pass vacuously.
		expect(
			DE_DE_AMOUNT.test(rendered) || EN_US_AMOUNT.test(rendered),
			`no amount carried a thousands separator, so locale shape is untestable: ${rendered}`
		).toBe(true);

		expect(rendered).not.toMatch(EN_US_AMOUNT);
		expect(rendered).toMatch(DE_DE_AMOUNT);
	});
});
