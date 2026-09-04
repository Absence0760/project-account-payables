import { describe, expect, it } from 'vitest';
import { formatAmountWithoutCurrency, recommendationCurrency } from './discountRecommendation';

describe('recommendationCurrency', () => {
	it('uses the row’s OWN currency, which is the question being asked', () => {
		expect(recommendationCurrency({ currency: 'EUR', unconvertible: false }, 'EUR')).toBe('EUR');
	});

	it('labels an unconvertible row with its own currency, not the totals’', () => {
		// This is the row the per-row field exists for. Its money is real and in
		// JPY; the totals are in USD. Before the field existed the card could
		// only render the figure bare — and before THAT it stamped the org
		// default on it, which is how "Save $412.00" described €412.
		expect(recommendationCurrency({ currency: 'JPY', unconvertible: true }, 'USD')).toBe('JPY');
	});

	it('normalises the code the response sent', () => {
		expect(recommendationCurrency({ currency: ' gbp ' }, 'USD')).toBe('GBP');
	});

	describe('a response that omits the per-row currency (older payload)', () => {
		// The field is additive, so the client must degrade rather than break.
		it('falls back to the totals currency when the row is not excluded', () => {
			// `unconvertible === false` means the offer's currency PROVABLY equals
			// the response's own — a safe stand-in.
			expect(recommendationCurrency({ unconvertible: false }, 'EUR')).toBe('EUR');
			expect(recommendationCurrency({}, 'USD')).toBe('USD');
			expect(recommendationCurrency({ unconvertible: null }, 'USD')).toBe('USD');
		});

		it('refuses to name a currency for an excluded row', () => {
			// The old payload says the offer is in some OTHER currency without
			// saying which, so the caller renders the figure symbol-free.
			expect(recommendationCurrency({ unconvertible: true }, 'USD')).toBeNull();
			expect(recommendationCurrency({ currency: null, unconvertible: true }, 'USD')).toBeNull();
		});

		it('returns null rather than guess when the totals currency is unusable too', () => {
			expect(recommendationCurrency({ unconvertible: false }, null)).toBeNull();
			expect(recommendationCurrency({ unconvertible: false }, '')).toBeNull();
			expect(recommendationCurrency({ unconvertible: false }, 'US DOLLAR')).toBeNull();
		});
	});

	it('ignores a malformed per-row code and falls back', () => {
		expect(recommendationCurrency({ currency: 'EUROS', unconvertible: false }, 'USD')).toBe('USD');
		expect(recommendationCurrency({ currency: 'EUROS', unconvertible: true }, 'USD')).toBeNull();
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
