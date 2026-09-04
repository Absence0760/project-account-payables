import { describe, expect, it } from 'vitest';
import { formatAmountWithoutCurrency, recommendationCurrency } from './discountRecommendation';

describe('recommendationCurrency', () => {
	it('labels a convertible recommendation with the response’s own totals currency', () => {
		// The whole point: the currency comes off the RESPONSE, never off an
		// org-default store the response does not agree with.
		expect(recommendationCurrency({ unconvertible: false }, 'EUR')).toBe('EUR');
	});

	it('normalises the code the response sent', () => {
		expect(recommendationCurrency({ unconvertible: false }, ' gbp ')).toBe('GBP');
	});

	it('refuses to name a currency for an unconvertible recommendation', () => {
		// `unconvertible` means "this offer is in a currency the totals are NOT
		// in" — and the response never says which. Stamping the totals currency
		// on it is how "Save $412.00" rendered a €412 saving.
		expect(recommendationCurrency({ unconvertible: true }, 'USD')).toBeNull();
	});

	it('treats a missing flag as convertible (the backend default)', () => {
		expect(recommendationCurrency({}, 'USD')).toBe('USD');
		expect(recommendationCurrency({ unconvertible: null }, 'USD')).toBe('USD');
	});

	it('returns null rather than guess when the totals currency is missing or malformed', () => {
		expect(recommendationCurrency({ unconvertible: false }, null)).toBeNull();
		expect(recommendationCurrency({ unconvertible: false }, '')).toBeNull();
		expect(recommendationCurrency({ unconvertible: false }, 'US DOLLAR')).toBeNull();
	});

	it('handles a null recommendation', () => {
		expect(recommendationCurrency(null, 'USD')).toBe('USD');
	});
});

describe('formatAmountWithoutCurrency', () => {
	it('renders grouping and two decimals with no symbol', () => {
		const out = formatAmountWithoutCurrency(1234.5);
		expect(out).not.toMatch(/[$£€]/);
		expect(out).toMatch(/1.234[.,]50/);
	});

	it('accepts an exact-decimal string', () => {
		expect(formatAmountWithoutCurrency('412')).toMatch(/412[.,]00/);
	});

	it('returns the placeholder for nothing / non-finite input', () => {
		expect(formatAmountWithoutCurrency(null)).toBe('—');
		expect(formatAmountWithoutCurrency(undefined)).toBe('—');
		expect(formatAmountWithoutCurrency('')).toBe('—');
		expect(formatAmountWithoutCurrency('not a number')).toBe('—');
		expect(formatAmountWithoutCurrency(Number.NaN)).toBe('—');
		expect(formatAmountWithoutCurrency(1234.5, 'n/a')).not.toBe('n/a');
	});
});
