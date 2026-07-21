/**
 * Locale-aware money formatting.
 *
 * Single source of truth for rendering currency across the app. Every
 * amount carries its own ISO 4217 currency code (USD, GBP, EUR, ZAR, …)
 * so a multi-currency tenant sees each line in its native symbol — never
 * a hardcoded `$`. Use `formatMoney` in scripts/`$derived`, and the
 * `<Money>` component in markup.
 *
 * Locale resolution: we pass `undefined` as the locale to
 * `Intl.NumberFormat`, which makes it honour the user's browser locale
 * (the runtime default). The currency *code* drives the symbol and the
 * minor-unit count; the *locale* drives grouping/decimal separators and
 * symbol placement. So a GBP amount renders "£1,234.50" for an en-US
 * visitor and "1.234,50 £" for a de-DE visitor — both correct.
 */

import { getActiveFormatLocale } from '$lib/i18n/formatLocale';

/** ISO 4217 default when an amount arrives without an explicit currency. */
export const DEFAULT_CURRENCY = 'USD';

export interface MoneyFormatOptions {
	/** ISO 4217 code. Falls back to {@link DEFAULT_CURRENCY} when empty/nullish. */
	currency?: string | null;
	/**
	 * BCP-47 locale. `undefined` (the default) uses the runtime/browser
	 * locale, which is what we want in the static SPA.
	 */
	locale?: string | undefined;
	/** Drop the fractional part (whole-currency display, e.g. dashboard KPIs). */
	whole?: boolean;
	/** Render as an accounting-style negative `($1,234.50)` rather than `-$1,234.50`. */
	accounting?: boolean;
}

/** Normalise a possibly-empty currency code to a safe ISO 4217 string. */
function resolveCurrency(currency?: string | null): string {
	const code = (currency ?? '').trim().toUpperCase();
	return code.length === 3 ? code : DEFAULT_CURRENCY;
}

/**
 * Coerce the many shapes amounts arrive in (number, string-Decimal from
 * the API, null) to a finite number. Returns `null` when there is no
 * usable value so the caller can render a placeholder.
 */
function toNumber(amount: number | string | null | undefined): number | null {
	if (amount === null || amount === undefined || amount === '') return null;
	const n = typeof amount === 'number' ? amount : Number(amount);
	return Number.isFinite(n) ? n : null;
}

/**
 * Format an amount as locale-aware currency.
 *
 * @returns the formatted string, or `placeholder` (default `—`) when the
 *          amount is null/empty/non-finite.
 */
export function formatMoney(
	amount: number | string | null | undefined,
	options: MoneyFormatOptions = {},
	placeholder = '—'
): string {
	const n = toNumber(amount);
	if (n === null) return placeholder;

	// Default to the active in-app locale (i18n picker) when the caller
	// passes none; falls back to `undefined` (browser locale) pre-selection.
	const locale = options.locale ?? getActiveFormatLocale();
	const currency = resolveCurrency(options.currency);
	const fmtOptions: Intl.NumberFormatOptions = {
		style: 'currency',
		currency,
		currencyDisplay: 'symbol'
	};
	if (options.whole) {
		fmtOptions.minimumFractionDigits = 0;
		fmtOptions.maximumFractionDigits = 0;
	}
	if (options.accounting) {
		// `currencySign: 'accounting'` wraps negatives in parentheses
		// using the locale's accounting convention.
		fmtOptions.currencySign = 'accounting';
	}

	try {
		return new Intl.NumberFormat(locale, fmtOptions).format(n);
	} catch {
		// An invalid currency code throws RangeError. Fall back to the
		// default currency rather than blow up a whole table render.
		return new Intl.NumberFormat(locale, {
			...fmtOptions,
			currency: DEFAULT_CURRENCY
		}).format(n);
	}
}

/**
 * Sum exact Decimal-string money amounts without introducing float
 * rounding artifacts (the classic `0.1 + 0.2` class of bug).
 *
 * Money arrives from the API as Decimal strings (e.g. "10.10"). Reducing
 * with `sum + Number(amount)` coerces each one to a binary float before
 * adding, so a running total can drift off the exact cent value even
 * though every individual amount is exact. This instead parses each
 * amount's sign/integer/fraction textually, scales every value to the
 * widest fraction length seen (via `BigInt`, so scaling never touches a
 * float), sums as exact integers, and only converts back to `number`
 * once at the very end — a single, lossless-for-display conversion
 * rather than one per addend.
 *
 * Null/undefined/empty/non-numeric entries are skipped (treated as 0),
 * mirroring the `Number(x ?? 0)` call sites this replaces. Returns `0`
 * for an empty or all-skipped input.
 */
export function sumMoney(amounts: Iterable<number | string | null | undefined>): number {
	let maxScale = 0;
	const parsed: { negative: boolean; digits: string; scale: number }[] = [];

	for (const raw of amounts) {
		if (raw === null || raw === undefined || raw === '') continue;
		const str = typeof raw === 'number' ? String(raw) : raw.trim();
		if (str === '') continue;
		const match = /^(-)?(\d+)(?:\.(\d+))?$/.exec(str);
		if (!match) continue; // not a plain decimal string — skip rather than throw on a display path
		const [, sign, intPart, fracPart = ''] = match;
		if (fracPart.length > maxScale) maxScale = fracPart.length;
		parsed.push({ negative: sign === '-', digits: intPart + fracPart, scale: fracPart.length });
	}

	if (parsed.length === 0) return 0;

	let totalScaled = 0n;
	for (const { negative, digits, scale } of parsed) {
		const scaledDigits = digits + '0'.repeat(maxScale - scale);
		const value = BigInt(scaledDigits);
		totalScaled += negative ? -value : value;
	}

	const divisor = 10 ** maxScale;
	return Number(totalScaled) / divisor;
}
