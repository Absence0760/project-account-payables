import { describe, it, expect } from 'vitest';
import { isPositiveAmount } from './money';

// `isPositiveAmount` is a *predicate* over the string-Decimals the API sends
// — it decides whether a figure is worth rendering at all (the 1099 report's
// card-excluded total / per-vendor card leg). It must never be mistaken for
// money arithmetic, so the cases below pin the boundaries: zero is not
// positive (there is nothing to show), and a missing/garbage figure is not
// positive either rather than throwing or rendering NaN.
describe('isPositiveAmount', () => {
	it('accepts a positive string-Decimal from the API', () => {
		expect(isPositiveAmount('1500.00')).toBe(true);
		expect(isPositiveAmount('0.01')).toBe(true);
	});

	it('accepts a positive number', () => {
		expect(isPositiveAmount(1500)).toBe(true);
	});

	it('rejects an exact zero in either shape', () => {
		// The 1099 card-excluded figure is "0.00" for the overwhelming majority
		// of vendors — showing an "excludes $0.00" qualifier there would be noise.
		expect(isPositiveAmount('0.00')).toBe(false);
		expect(isPositiveAmount('0')).toBe(false);
		expect(isPositiveAmount(0)).toBe(false);
	});

	it('rejects a negative amount', () => {
		expect(isPositiveAmount('-10.00')).toBe(false);
		expect(isPositiveAmount(-10)).toBe(false);
	});

	it('rejects absent / unparseable figures instead of throwing', () => {
		expect(isPositiveAmount(null)).toBe(false);
		expect(isPositiveAmount(undefined)).toBe(false);
		expect(isPositiveAmount('')).toBe(false);
		expect(isPositiveAmount('not-a-number')).toBe(false);
		expect(isPositiveAmount(Number.NaN)).toBe(false);
		expect(isPositiveAmount(Number.POSITIVE_INFINITY)).toBe(false);
	});
});
