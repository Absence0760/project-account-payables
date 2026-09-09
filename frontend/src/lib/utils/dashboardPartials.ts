/**
 * When a dashboard CHART is built partly from unconverted rows.
 *
 * A foreign invoice with no locked exchange rate into the org's reporting
 * currency is summed at FACE value rather than dropped — the right call for a
 * spend figure, because dropping it would understate, but only honest if the
 * page says so (`docs/decisions.md` §35). The API carries a count on each of
 * the three chart blocks, at the grain each chart is READ at:
 *
 *   - `vendor_spend[].unconverted_count` — per vendor, because the tile RANKS
 *     vendors against each other and an unconverted total is not comparable
 *     with a converted one;
 *   - `aging_reporting.unconverted_count` — one for the whole band set;
 *   - `monthly_trend[].unconverted_count` — per month, because a trend is read
 *     bar against bar and a whole-series count would not say which step in the
 *     line not to trust.
 *
 * The KPI row's counts (`reporting`, `total_paid`, `total_pending`) are a
 * different question and stay in the page.
 *
 * Extracted for the same reason `discountPartialSet.ts` was: a `reduce` inline
 * in the template would force its unit test to restate the sum it is checking.
 * Pure — no `$state`, no `fetch` — so it unit-tests under the plain-Node vitest
 * config.
 */

/** The projection of a `vendor_spend` / `monthly_trend` entry these helpers
 *  read. Deliberately narrower than either full row type: the rule depends on
 *  the count and the label, and nothing else. */
export interface PartialSeriesEntry {
	unconverted_count: number;
}

/** Total invoices a series folded at face value.
 *
 * A negative, fractional or non-finite count is treated as zero rather than
 * propagated: the counts come off the wire, and a malformed one must not turn
 * the disclosure into a nonsense figure — or, worse, cancel a real one out.
 */
export function totalUnconverted(entries: readonly PartialSeriesEntry[] | null | undefined): number {
	if (!Array.isArray(entries)) return 0;
	return entries.reduce((n, e) => n + safeCount(e?.unconverted_count), 0);
}

/** The labels of the entries that folded at least one row at face value, in the
 *  series' own order.
 *
 * The count alone says "something here mixes currencies" and leaves the reader
 * to guess where; naming the vendors (or the months) is what makes the notice
 * act on the thing the chart is for.
 */
export function partialLabels<T extends PartialSeriesEntry>(
	entries: readonly T[] | null | undefined,
	label: (entry: T) => string
): string[] {
	if (!Array.isArray(entries)) return [];
	return entries.filter((e) => safeCount(e?.unconverted_count) > 0).map(label);
}

function safeCount(value: number | null | undefined): number {
	if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return 0;
	return Math.floor(value);
}
