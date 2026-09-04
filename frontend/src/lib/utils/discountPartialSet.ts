/**
 * When the discount dashboard's REALISED figures describe only part of the set.
 *
 * `captured_amount` and `missed_amount` count only offers denominated in the
 * dashboard's own `currency` — amounts in different currencies are never added
 * together, which is what makes each figure honest. The cost of that honesty is
 * that a multi-currency tenant's green and red KPIs silently describe a subset,
 * so the page has to say so. `unconvertible_offer_count` already provides the
 * same disclosure for `projected_savings`.
 *
 * This lived as an inline `a + b > 0` expression in the page template, which
 * meant a unit test would have had to restate the sum it was checking. Extracted
 * so the rule has one owner and the test asserts against behaviour instead of
 * against its own arithmetic.
 *
 * Pure — no `$state`, no `fetch` — so it sits in `utils/` beside
 * `reportingCurrency.ts` and is unit-tested under the plain-Node vitest config.
 */

/** The projection of `DiscountDashboard` these helpers read. Deliberately
 *  narrower than the full interface: the rule depends on the two exclusion
 *  counts and nothing else. */
export interface RealisedExclusionCounts {
	excluded_captured_count: number;
	excluded_missed_count: number;
}

/** How many realised offers are missing from `captured_amount` + `missed_amount`.
 *
 * A negative or non-finite count is treated as zero rather than propagated: the
 * counts come off the wire, and a malformed one must not turn the disclosure
 * banner into a nonsense figure (or, worse, cancel a real exclusion out).
 */
export function partialRealisedCount(dashboard: RealisedExclusionCounts | null | undefined): number {
	if (!dashboard) return 0;
	const captured = safeCount(dashboard.excluded_captured_count);
	const missed = safeCount(dashboard.excluded_missed_count);
	return captured + missed;
}

/** Whether the realised KPIs describe only part of the set, and the page
 *  therefore owes the reader a disclosure. */
export function hasPartialRealisedSet(
	dashboard: RealisedExclusionCounts | null | undefined
): boolean {
	return partialRealisedCount(dashboard) > 0;
}

function safeCount(value: number | null | undefined): number {
	if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return 0;
	return Math.floor(value);
}
