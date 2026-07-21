import { describe, expect, it } from 'vitest';
import { isPositiveAmount, sumMoney } from './money';

// sumMoney sums exact Decimal-string money amounts without going through
// JS floats. The bug it fixes: `sum + Number(amount)` reduces coerce each
// string to a binary float before adding, which can drift off the exact
// cent value for the same reason `0.1 + 0.2 !== 0.3` in JS — even though
// every individual amount is an exact Decimal.

describe('sumMoney', () => {
	it('sums plain decimal strings without float drift', () => {
		// The canonical repro: Number('0.1') + Number('0.2') === 0.30000000000000004
		expect(sumMoney(['0.1', '0.2'])).toBe(0.3);
	});

	it('sums a longer run of two-decimal amounts exactly', () => {
		// A naive float reduce accumulates error across many additions —
		// this must land on the exact cent total, not something like
		// 60.099999999999994.
		expect(sumMoney(['10.10', '20.20', '15.35', '14.45'])).toBe(60.1);
	});

	it('treats null/undefined/empty entries as zero', () => {
		expect(sumMoney(['10.00', null, undefined, '', '5.00'])).toBe(15);
	});

	it('handles negative amounts (e.g. a discount/credit line)', () => {
		expect(sumMoney(['100.00', '-25.50'])).toBe(74.5);
	});

	it('accepts numbers as well as strings', () => {
		expect(sumMoney([10, 20.5, '5.25'])).toBe(35.75);
	});

	it('returns 0 for an empty input', () => {
		expect(sumMoney([])).toBe(0);
	});

	it('returns 0 when every entry is null/undefined/empty', () => {
		expect(sumMoney([null, undefined, ''])).toBe(0);
	});

	it('skips non-numeric garbage rather than throwing on a display path', () => {
		expect(sumMoney(['10.00', 'not-a-number', '5.00'])).toBe(15);
	});

	it('handles amounts of differing decimal scale (e.g. a 0-decimal currency mixed with 2-decimal)', () => {
		expect(sumMoney(['100', '0.5'])).toBe(100.5);
	});

	it('accepts any iterable, not just arrays', () => {
		function* gen() {
			yield '1.00';
			yield '2.00';
		}
		expect(sumMoney(gen())).toBe(3);
	});
});

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
