import { test, expect } from 'vitest';
import {
	DEFAULT_LOCALE,
	SUPPORTED_LOCALES,
	dirForLocale,
	isSupportedLocale,
	negotiateLocale,
	parseAcceptLanguage,
} from './locale';

test('SUPPORTED_LOCALES ships at least en + de this slice', () => {
	expect(SUPPORTED_LOCALES).toContain('en');
	expect(SUPPORTED_LOCALES).toContain('de');
	expect(DEFAULT_LOCALE).toBe('en');
});

test('isSupportedLocale only accepts shipped locales', () => {
	expect(isSupportedLocale('en')).toBe(true);
	expect(isSupportedLocale('de')).toBe(true);
	expect(isSupportedLocale('fr')).toBe(false);
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

test('defaults to English when nothing matches', () => {
	expect(negotiateLocale(null, ['fr-FR', 'ja'])).toBe('en');
	expect(negotiateLocale(null, null)).toBe('en');
	expect(negotiateLocale(undefined, undefined)).toBe('en');
});
