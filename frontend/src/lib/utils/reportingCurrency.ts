/**
 * Resolving a tenant's REPORTING (base) currency out of `GET /api/organization`.
 *
 * The resolution order here is not a preference — it is a mirror of
 * `backend/app/services/currency_conversion.py::resolve_reporting_currency`,
 * the function that decides what currency the API's cross-currency rollups are
 * *actually denominated in*: `/api/payments/summary`, the CFO forecast + cash
 * position, the dashboard's `reporting` block, the discount dashboard.
 *
 * `orgSettings.svelte.ts` read only `invoice_defaults.currency`, which is the
 * LAST candidate. That is a mislabel rather than a fallback: an org reporting
 * in GBP while its invoice default stayed USD had its converted GBP totals
 * rendered with a `$`, on every aggregate figure in the app. The two keys agree
 * in the common case, which is exactly why it went unnoticed.
 *
 * Pure — no `$state`, no `fetch` — so it lives in `utils/` beside `money.ts`
 * and is unit-tested under the plain-Node vitest config. (The store itself is
 * a `.svelte.ts` rune module and can't be imported there.)
 */

/** The settings shape this resolver reads. A projection of the org response —
 *  a non-admin caller only receives these three, by the allow-list in
 *  `backend/app/services/org_settings_view.py`. */
export interface ReportingCurrencySettings {
	reporting_currency?: string | null;
	payments?: { home_currency?: string | null } | null;
	invoice_defaults?: { currency?: string | null } | null;
}

/** A usable ISO 4217 code, or `null`. */
function usableCode(value: string | null | undefined): string | null {
	const code = (value ?? '').trim();
	return code.length === 3 ? code.toUpperCase() : null;
}

/**
 * Resolve the reporting currency, or `null` when the org declares none usable
 * — the caller keeps its own platform default rather than guessing here.
 */
export function resolveReportingCurrency(
	settings: ReportingCurrencySettings | null | undefined
): string | null {
	return (
		usableCode(settings?.reporting_currency) ??
		usableCode(settings?.payments?.home_currency) ??
		usableCode(settings?.invoice_defaults?.currency)
	);
}
