/**
 * Grouping currency-tagged money amounts into per-currency subtotals.
 *
 * The house rule for a multi-currency rollup is **be honest about what could
 * not be combined rather than render one wrong number** — the same rule
 * `/cfo` applies with `unconverted_count` and `/discounts` with
 * `unconvertible_count`. A screen that sums EUR 100 and USD 100 into "200"
 * is not reporting a total; it is reporting a number that is not denominated
 * in anything real.
 *
 * This is the display-side primitive for that rule: it never converts (an FX
 * rate fetched on a read makes the figure non-deterministic — see
 * `backend/docs/multi-currency.md`), it never adds across currencies, and it
 * sums *within* each currency through {@link sumMoney} so every subtotal stays
 * exact (Decimal strings scaled through `BigInt`, never a float reduce).
 *
 * Pure — no `$state`, no `fetch`, no browser globals — so it lives in `utils/`
 * beside `money.ts` and is unit-tested under the plain-Node vitest config.
 */

import { DEFAULT_CURRENCY, sumMoney, type MoneyAmount } from './money';

/** One currency's slice of a mixed-currency selection. */
export interface CurrencyGroup {
	/** Resolved ISO 4217 code, uppercased. */
	currency: string;
	/** Exact sum of this currency's amounts (via `sumMoney`). */
	total: number;
	/** How many rows contributed to this subtotal. */
	count: number;
}

/** A row carrying an amount and the currency it is denominated in. */
export interface CurrencyTaggedAmount {
	amount: MoneyAmount;
	currency?: string | null;
}

/** Normalise a possibly-empty currency code the same way `money.ts` does. */
function resolveCurrency(currency: string | null | undefined, fallback: string): string {
	const code = (currency ?? '').trim().toUpperCase();
	return code.length === 3 ? code : fallback;
}

/**
 * Bucket `rows` by currency and sum each bucket exactly.
 *
 * Rows with no usable currency code fall back to `fallbackCurrency` (the org's
 * reporting currency at the call site) rather than being dropped — dropping
 * them would understate the selection, which is the failure mode this whole
 * helper exists to prevent.
 *
 * Ordering is by currency code ascending: deterministic, and stable as the
 * amounts move (a total-ordered list would reshuffle mid-selection).
 *
 * Returns `[]` for an empty input — the caller decides what "nothing selected"
 * reads as, because that is a display decision, not a money one.
 */
export function groupAmountsByCurrency(
	rows: Iterable<CurrencyTaggedAmount>,
	fallbackCurrency: string = DEFAULT_CURRENCY
): CurrencyGroup[] {
	const fallback = resolveCurrency(fallbackCurrency, DEFAULT_CURRENCY);
	const buckets = new Map<string, MoneyAmount[]>();

	for (const row of rows) {
		const code = resolveCurrency(row.currency, fallback);
		const existing = buckets.get(code);
		if (existing) existing.push(row.amount);
		else buckets.set(code, [row.amount]);
	}

	return [...buckets.entries()]
		.map(([currency, amounts]) => ({
			currency,
			total: sumMoney(amounts),
			count: amounts.length
		}))
		.sort((a, b) => a.currency.localeCompare(b.currency));
}

/**
 * Does this set of groups span more than one currency?
 *
 * A named predicate rather than `groups.length > 1` at the call site, because
 * the *consequence* is specific: `services/payment_runs.create_payment_run_for_invoices`
 * refuses a payment run spanning more than one currency with a 422
 * ("All invoices in a payment run must share the same currency"), so a mixed
 * selection is not merely awkward to display — it cannot be submitted at all.
 */
export function spansMultipleCurrencies(groups: CurrencyGroup[]): boolean {
	return groups.length > 1;
}
