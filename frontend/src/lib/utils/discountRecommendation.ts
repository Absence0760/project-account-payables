/**
 * Which currency an optimizer recommendation's money is denominated in.
 *
 * `POST /api/discounts/optimize` returns three money TOTALS in one stated
 * `currency` (the org's reporting currency) plus a ranked list of
 * recommendations. Each recommendation's `roi.savings` is computed from its own
 * OFFER's `base_amount`, so it is in the OFFER's currency — which the response
 * never names per row. What it does name is `unconvertible`: the backend sets it
 * on exactly the rows whose currency is not the totals' currency
 * (`backend/app/services/discount_optimizer.py`), and excludes those rows from
 * every total.
 *
 * That flag is therefore the whole contract, in both directions:
 *
 *   * `unconvertible === false` — the offer's currency PROVABLY equals the
 *     response's `currency`, so the figure can be labelled with it.
 *   * `unconvertible === true`  — the offer is in some OTHER currency, and the
 *     response does not say which. There is no honest code to stamp on it.
 *
 * The page used to label every card with the ORG default currency read from a
 * separate store, which is right for the convertible rows by coincidence and
 * wrong for exactly the rows the flag marks: a €412 saving rendered as
 * "Save $412.00", two lines below a banner saying those offers are in another
 * currency.
 *
 * Pure — no `$state`, no `fetch` — so it sits in `utils/` beside
 * `discountPartialSet.ts` and is unit-tested under the plain-Node vitest config.
 */

import { getActiveFormatLocale } from '$lib/i18n/formatLocale';

/** The projection of `DiscountRecommendation` this rule reads. Deliberately
 *  narrower than the full interface — the currency question depends on the one
 *  flag and nothing else. */
export interface UnconvertibleFlag {
	unconvertible?: boolean | null;
}

/**
 * The ISO 4217 code to render a recommendation's money with, or `null` when it
 * cannot be established.
 *
 * `null` is a real answer, not a failure: a caller must render the figure
 * without a currency symbol rather than pick one, because any code it picked
 * would be a claim the response does not support. A missing / malformed
 * `totalsCurrency` also yields `null` for the same reason.
 */
export function recommendationCurrency(
	rec: UnconvertibleFlag | null | undefined,
	totalsCurrency: string | null | undefined
): string | null {
	if (rec?.unconvertible) return null;
	const code = (totalsCurrency ?? '').trim().toUpperCase();
	return /^[A-Z]{3}$/.test(code) ? code : null;
}

/**
 * A money figure formatted with grouping and two decimals but NO currency
 * symbol — the honest rendering when {@link recommendationCurrency} returns
 * `null`.
 *
 * Not a hand-rolled currency formatter (the ban in `frontend/CLAUDE.md`
 * § Money formatting is on inventing a *currency* format): this deliberately
 * renders no symbol at all, which is the point. It follows the same active
 * locale `formatMoney` does, so grouping and decimal separators stay consistent
 * with every labelled figure beside it.
 *
 * Null / non-finite input returns the placeholder rather than `NaN`.
 */
export function formatAmountWithoutCurrency(
	amount: number | string | null | undefined,
	placeholder = '—'
): string {
	if (amount === null || amount === undefined || amount === '') return placeholder;
	const n = typeof amount === 'number' ? amount : Number(amount);
	if (!Number.isFinite(n)) return placeholder;
	return new Intl.NumberFormat(getActiveFormatLocale(), {
		minimumFractionDigits: 2,
		maximumFractionDigits: 2
	}).format(n);
}
