import { describe, expect, it } from 'vitest';
import { isMoneyInput, normalizeMoneyInput } from './moneyInput';

describe('normalizeMoneyInput', () => {
	it('returns the typed digits unchanged, so exactness survives the trip', () => {
		// The defect this exists to prevent: `Number("9799.999999999999999")` is
		// 9800 — a different budget, and a different set of invoices paid early.
		expect(normalizeMoneyInput('9799.999999999999999')).toBe('9799.999999999999999');
	});

	it('trims surrounding whitespace but never rewrites the number', () => {
		expect(normalizeMoneyInput('  1234.50  ')).toBe('1234.50');
		// Trailing zeros are significant to a Decimal reader; they stay.
		expect(normalizeMoneyInput('100.00')).toBe('100.00');
	});

	it('accepts a whole amount', () => {
		expect(normalizeMoneyInput('1000')).toBe('1000');
	});

	it('rejects anything that is not a plain non-negative decimal', () => {
		for (const bad of [
			'',
			'   ',
			'abc',
			'1,000.00', // separators are locale-ambiguous
			'$100',
			'1e3',
			'-5.00', // the backend constrains a cash budget to >= 0
			'+5.00',
			'100.',
			'.5',
			'1.2.3',
			'NaN',
			'Infinity'
		]) {
			expect(normalizeMoneyInput(bad), bad).toBeNull();
		}
	});

	it('rejects absurdly long input rather than sending it', () => {
		expect(normalizeMoneyInput('1'.repeat(16))).toBeNull();
		expect(normalizeMoneyInput(`1.${'0'.repeat(16)}`)).toBeNull();
	});

	it('passes through more precision than money needs, rather than truncating it', () => {
		// A client that quietly refused what the server parses exactly would be a
		// narrower version of the same rounding bug.
		expect(normalizeMoneyInput('1.000000000000001')).toBe('1.000000000000001');
	});

	it('handles null / undefined', () => {
		expect(normalizeMoneyInput(null)).toBeNull();
		expect(normalizeMoneyInput(undefined)).toBeNull();
	});
});

describe('isMoneyInput', () => {
	it('agrees with normalizeMoneyInput', () => {
		expect(isMoneyInput('12.34')).toBe(true);
		expect(isMoneyInput('')).toBe(false);
		expect(isMoneyInput('twelve')).toBe(false);
	});
});
