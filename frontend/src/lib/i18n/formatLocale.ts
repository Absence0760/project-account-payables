// The active BCP-47 locale used by the `Intl`-based formatters
// (`utils/money.ts::formatMoney`, and any date helper that opts in). This
// is a deliberately tiny, framework-free holder — NOT a rune — so the pure
// `money.ts` module can read it without importing the Svelte runtime (it is
// imported by non-component code and unit tests).
//
// The i18n store (store.svelte.ts) writes this from `setLocale`, so picking
// German in the locale picker makes "$1,234.50" become "1.234,50 $"
// (grouping/decimal separators + symbol placement follow the locale; the
// ISO 4217 currency code still drives the symbol + minor units).
//
// `undefined` (the initial value) means "use the runtime/browser locale",
// which is exactly the pre-i18n behaviour — so nothing regresses until a
// locale is actively selected.

let activeFormatLocale: string | undefined = undefined;

/** Set the locale the `Intl` formatters default to. Empty/nullish resets to browser default. */
export function setActiveFormatLocale(locale: string | null | undefined): void {
	activeFormatLocale = locale?.trim() || undefined;
}

/** The active format locale, or `undefined` to defer to the browser default. */
export function getActiveFormatLocale(): string | undefined {
	return activeFormatLocale;
}
