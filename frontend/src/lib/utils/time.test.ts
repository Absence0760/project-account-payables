import { afterEach, describe, expect, it } from 'vitest';
import { formatDate, formatPeriod } from './time';
import { setActiveFormatLocale } from '$lib/i18n/formatLocale';

// The date helpers drive their locale off the active in-app i18n locale
// (the same holder money.ts reads). These tests assert the locale actually
// flows through and that null/invalid inputs fall back gracefully.

afterEach(() => {
	// Reset the shared holder so locale state never leaks across tests.
	setActiveFormatLocale(undefined);
});

describe('formatDate', () => {
	it('returns the placeholder for null/empty/invalid', () => {
		expect(formatDate(null)).toBe('—');
		expect(formatDate('')).toBe('—');
		expect(formatDate(undefined)).toBe('—');
		expect(formatDate('not-a-date')).toBe('—');
		expect(formatDate(null, 'n/a')).toBe('n/a');
	});

	it('formats a date under the explicit en-US locale', () => {
		setActiveFormatLocale('en-US');
		// Use a midday UTC timestamp so the calendar day is locale/timezone-stable.
		expect(formatDate('2026-06-20T12:00:00Z')).toBe('Jun 20, 2026');
	});

	it('follows the active locale (German month abbreviation differs)', () => {
		setActiveFormatLocale('en-US');
		const en = formatDate('2026-03-20T12:00:00Z');
		setActiveFormatLocale('de-DE');
		const de = formatDate('2026-03-20T12:00:00Z');
		// March is "Mar" in en-US and "März" in de-DE — the locale must change
		// the output, proving the helper is driven off the active locale.
		expect(en).not.toBe(de);
		expect(en).toContain('Mar');
		expect(de).toContain('März');
	});
});

describe('formatPeriod', () => {
	it('returns the raw key for an unparseable value', () => {
		expect(formatPeriod('garbage')).toBe('garbage');
	});

	it('renders a month key (YYYY-MM) with no day-of-month', () => {
		setActiveFormatLocale('en-US');
		// Month key → "<Mon> <YY>" (year 2-digit, no day).
		expect(formatPeriod('2026-06')).toBe('Jun 26');
	});

	it('renders a day key (YYYY-MM-DD) with the day-of-month', () => {
		setActiveFormatLocale('en-US');
		expect(formatPeriod('2026-06-20')).toBe('Jun 20, 26');
	});

	it('follows the active locale', () => {
		setActiveFormatLocale('en-US');
		const en = formatPeriod('2026-03');
		setActiveFormatLocale('de-DE');
		const de = formatPeriod('2026-03');
		expect(en).not.toBe(de);
		expect(en).toContain('Mar');
		expect(de).toContain('März');
	});
});
