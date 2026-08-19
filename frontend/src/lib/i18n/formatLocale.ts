// The active BCP-47 locale used by the `Intl`-based formatters
// (`utils/money.ts::formatMoney`, `utils/time.ts`'s date + relative-time
// helpers). This is a deliberately tiny holder — NOT a `.svelte.ts` rune
// module — so the pure `money.ts` / `time.ts` modules can read it without
// pulling in the app's Svelte runtime (they are imported by non-component
// code and unit-tested under vitest's plain-node environment).
//
// The i18n store (store.svelte.ts) writes this from `setLocale`, so picking
// German in the locale picker makes "$1,234.50" become "1.234,50 $"
// (grouping/decimal separators + symbol placement follow the locale; the
// ISO 4217 currency code still drives the symbol + minor units).
//
// `undefined` (the initial value) means "use the runtime/browser locale",
// which is exactly the pre-i18n behaviour — so nothing regresses until a
// locale is actively selected.
//
// ## Why this reads reactively without being a rune
//
// A plain module-level `let` carries no reactive dependency, so every money
// cell and date stayed in the browser locale after a locale switch until its
// component happened to remount — the labels moved (the `dict` rune re-runs
// every `m()` call site) and the figures did not. `createSubscriber` is the
// Svelte primitive for exactly this case: an externally-mutated value a getter
// can make reactive. `getActiveFormatLocale()` calls `subscribe()`, so any
// `$derived` / template that formats money or a date registers a dependency,
// and `setActiveFormatLocale` invalidates all of them at once.
//
// Crucially this does NOT collapse the layering. `svelte/reactivity` resolves
// to `index-server.js` outside the browser — where `createSubscriber` is a
// no-op returning a no-op — so `money.ts` / `time.ts` stay importable under
// vitest's node environment with no Svelte runtime, no compiler and no
// `$app/*` resolution involved. The value half of this module is plain JS
// either way; only the invalidation half is Svelte-aware, and only in a
// browser. Do not "simplify" this into a `$state` rune: that would make
// `money.ts` a Svelte-compiler dependency and break its unit tests.

import { createSubscriber } from 'svelte/reactivity';

let activeFormatLocale: string | undefined = undefined;

/**
 * Invalidates every reactive reader of {@link getActiveFormatLocale}. Captured
 * while at least one effect is subscribed; `null` before the first subscriber
 * and after the last one is torn down (nothing to notify in either case), and
 * always `null` outside the browser.
 */
let notifyLocaleChanged: (() => void) | null = null;

const subscribe = createSubscriber((update) => {
	notifyLocaleChanged = update;
	return () => {
		notifyLocaleChanged = null;
	};
});

/** Set the locale the `Intl` formatters default to. Empty/nullish resets to browser default. */
export function setActiveFormatLocale(locale: string | null | undefined): void {
	const next = locale?.trim() || undefined;
	// A no-change write must not invalidate — `applyDocumentLocale` re-applies
	// the same locale on a re-entrant `setLocale`, and re-rendering every money
	// cell in the app for that would be pure waste.
	if (next === activeFormatLocale) return;
	activeFormatLocale = next;
	notifyLocaleChanged?.();
}

/**
 * The active format locale, or `undefined` to defer to the browser default.
 *
 * Read inside a `$derived` / template — which every `formatMoney` /
 * `formatDate` / `timeAgo` call site is — and the read is reactive: a later
 * `setActiveFormatLocale` re-renders it.
 */
export function getActiveFormatLocale(): string | undefined {
	// Registers the calling effect / derived as a dependency. A no-op outside a
	// tracking context (and outside the browser), so the value below is returned
	// unchanged either way.
	subscribe();
	return activeFormatLocale;
}
