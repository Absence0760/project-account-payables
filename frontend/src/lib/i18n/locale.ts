// Pure locale negotiation + direction helpers for the web i18n runtime.
//
// No Svelte runtime, no message catalogue — kept side-effect-free so it
// unit-tests under vitest without a browser. The reactive runtime (the
// locale rune + message lookup) lives in store.svelte.ts; the message
// catalogues live in locales/.
//
// The frontend is statically prerendered (adapter-static, GitHub Pages —
// no per-request SSR), so locale is negotiated client-side from
// navigator.languages + a stored preference rather than from an
// Accept-Language header on the server. The negotiation here is still
// written to accept a full Accept-Language-style q-list so the same parser
// would work if a server surface ever needed it.
//
// First slice ships `en` + `de`; the structure (SUPPORTED_LOCALES + the
// match maps below) is built so the full `en, de, fr, es, pt-BR, ja` set
// drops in by extending the four tables, with no other changes.

export const SUPPORTED_LOCALES = ['en', 'de'] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];
export const DEFAULT_LOCALE: Locale = 'en';

// Endonyms (the language's own name) for the locale picker — never
// translated, always shown in the target language's own script.
export const LOCALE_LABELS: Record<Locale, string> = {
	en: 'English',
	de: 'Deutsch',
};

// Case-insensitive exact-tag map. Keys are lowercased; values are the
// canonical-cased Locale we actually use (`pt-BR`, not `pt-br` — relevant
// once that locale ships).
const EXACT: Record<string, Locale> = {
	en: 'en',
	de: 'de',
};

// Base-language fallback: a tag we don't carry exactly (de-AT, en-GB)
// still resolves to the one variant we ship for that language.
const BASE_TO_LOCALE: Record<string, Locale> = {
	en: 'en',
	de: 'de',
};

// RTL base languages. None of the current set is RTL, but the switch-point
// exists so dropping in an Arabic/Hebrew catalogue later flips <html dir>
// with no further plumbing (the web shell already leans on the dark theme
// + flex layout; audit CSS for logical properties when that lands).
const RTL_BASES = new Set(['ar', 'he', 'fa', 'ur']);

export function isSupportedLocale(value: string | null | undefined): value is Locale {
	return value != null && (SUPPORTED_LOCALES as readonly string[]).includes(value);
}

export function dirForLocale(locale: string): 'ltr' | 'rtl' {
	const base = locale.toLowerCase().split('-')[0];
	return RTL_BASES.has(base) ? 'rtl' : 'ltr';
}

function exactMatch(tag: string): Locale | null {
	return EXACT[tag.toLowerCase()] ?? null;
}

function baseMatch(tag: string): Locale | null {
	const base = tag.toLowerCase().split('-')[0];
	return BASE_TO_LOCALE[base] ?? null;
}

// Parse an Accept-Language-style header (or a single navigator.language
// tag, or a comma-joined navigator.languages list) into the tags ordered
// by descending q-weight. `*` and empty tags are dropped.
export function parseAcceptLanguage(header: string): string[] {
	return header
		.split(',')
		.map((part) => {
			const [rawTag, ...params] = part.trim().split(';');
			let q = 1;
			for (const p of params) {
				const mt = p.trim().match(/^q=([0-9.]+)$/);
				if (mt) q = Number.parseFloat(mt[1]);
			}
			return { tag: rawTag.trim(), q: Number.isFinite(q) ? q : 0 };
		})
		.filter((x) => x.tag.length > 0 && x.tag !== '*')
		.sort((a, b) => b.q - a.q)
		.map((x) => x.tag);
}

// Resolve the best supported locale. A stored preference (our own
// canonical value, written by setLocale) wins outright; otherwise the
// ordered navigator.languages tags are matched exact first, then by base
// language; falling back to DEFAULT_LOCALE (English).
export function negotiateLocale(
	stored?: string | null,
	navigatorLanguages?: string | string[] | null,
): Locale {
	if (isSupportedLocale(stored)) return stored;
	if (stored) {
		const m = exactMatch(stored) ?? baseMatch(stored);
		if (m) return m;
	}
	if (!navigatorLanguages) return DEFAULT_LOCALE;
	const header = Array.isArray(navigatorLanguages)
		? navigatorLanguages.join(',')
		: navigatorLanguages;
	// Walk tags in descending q-order; for each, try an exact match then a
	// base-language match before moving to the next, lower-priority tag. A
	// naive "all exact matches first, then all base matches" pass would let
	// a low-priority exact tag beat a higher-priority tag we only carry by
	// base language.
	const tags = parseAcceptLanguage(header);
	for (const tag of tags) {
		const match = exactMatch(tag) ?? baseMatch(tag);
		if (match) return match;
	}
	return DEFAULT_LOCALE;
}
