import { test, expect } from 'vitest';
import {
	DEFAULT_LOCALE,
	SUPPORTED_LOCALES,
	dirForLocale,
	isSupportedLocale,
	negotiateLocale,
	parseAcceptLanguage,
} from './locale';

test('SUPPORTED_LOCALES ships the full en, de, fr, es, pt-BR, ja set', () => {
	expect(SUPPORTED_LOCALES).toContain('en');
	expect(SUPPORTED_LOCALES).toContain('de');
	expect(SUPPORTED_LOCALES).toContain('fr');
	expect(SUPPORTED_LOCALES).toContain('es');
	expect(SUPPORTED_LOCALES).toContain('pt-BR');
	expect(SUPPORTED_LOCALES).toContain('ja');
	expect(DEFAULT_LOCALE).toBe('en');
});

test('isSupportedLocale only accepts shipped locales', () => {
	expect(isSupportedLocale('en')).toBe(true);
	expect(isSupportedLocale('de')).toBe(true);
	expect(isSupportedLocale('fr')).toBe(true);
	expect(isSupportedLocale('es')).toBe(true);
	expect(isSupportedLocale('pt-BR')).toBe(true);
	expect(isSupportedLocale('ja')).toBe(true);
	// Case-sensitive on the canonical value: the negotiator lowercases tags,
	// but isSupportedLocale checks the exact canonical string.
	expect(isSupportedLocale('pt-br')).toBe(false);
	// A language we don't ship.
	expect(isSupportedLocale('zh')).toBe(false);
	expect(isSupportedLocale(null)).toBe(false);
	expect(isSupportedLocale(undefined)).toBe(false);
});

test('dirForLocale is LTR for the current set, with the RTL switch-point present', () => {
	expect(dirForLocale('en')).toBe('ltr');
	expect(dirForLocale('de')).toBe('ltr');
	// Future RTL catalogues flip <html dir> with no further plumbing.
	expect(dirForLocale('ar')).toBe('rtl');
	expect(dirForLocale('he-IL')).toBe('rtl');
});

test('parseAcceptLanguage orders tags by descending q-weight and drops *', () => {
	expect(parseAcceptLanguage('de,en;q=0.5')).toEqual(['de', 'en']);
	expect(parseAcceptLanguage('en;q=0.3,de;q=0.9')).toEqual(['de', 'en']);
	expect(parseAcceptLanguage('*,de')).toEqual(['de']);
});

test('a stored canonical choice wins outright', () => {
	expect(negotiateLocale('de', ['en-US'])).toBe('de');
	expect(negotiateLocale('en', ['de'])).toBe('en');
});

test('a stored regional/base tag resolves to the shipped variant', () => {
	expect(negotiateLocale('de-AT', null)).toBe('de');
	expect(negotiateLocale('en-GB', null)).toBe('en');
});

test('falls back to navigator.languages when no stored choice', () => {
	expect(negotiateLocale(null, ['de-DE', 'en-US'])).toBe('de');
	expect(negotiateLocale(null, ['en-US', 'de-DE'])).toBe('en');
});

test('accepts navigator.languages as a comma-joined string too', () => {
	expect(negotiateLocale(null, 'de-DE,en-US')).toBe('de');
});

test('a higher-priority base-only match beats a lower-priority exact match', () => {
	// de-AT (we ship the de base) is the top preference; it must win over the
	// lower-q en, even though en is an exact tag.
	expect(negotiateLocale(null, ['de-AT', 'en;q=0.5'])).toBe('de');
});

test('the new locales negotiate from navigator tags', () => {
	expect(negotiateLocale(null, ['fr-FR', 'en'])).toBe('fr');
	expect(negotiateLocale(null, ['es-MX', 'en'])).toBe('es');
	expect(negotiateLocale(null, ['ja'])).toBe('ja');
	// pt-BR resolves exactly; any other pt-* (or bare pt) maps to our pt-BR.
	expect(negotiateLocale(null, ['pt-BR'])).toBe('pt-BR');
	expect(negotiateLocale(null, ['pt-PT'])).toBe('pt-BR');
	expect(negotiateLocale(null, ['pt'])).toBe('pt-BR');
	// A stored canonical pt-BR choice round-trips.
	expect(negotiateLocale('pt-BR', ['en'])).toBe('pt-BR');
});

test('defaults to English when nothing matches', () => {
	// zh / ko are not shipped, so neither resolves.
	expect(negotiateLocale(null, ['zh-CN', 'ko'])).toBe('en');
	expect(negotiateLocale(null, null)).toBe('en');
	expect(negotiateLocale(undefined, undefined)).toBe('en');
});
