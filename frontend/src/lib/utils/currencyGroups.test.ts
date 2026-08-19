import { describe, it, expect } from 'vitest';
import { groupAmountsByCurrency, spansMultipleCurrencies } from './currencyGroups';

describe('groupAmountsByCurrency', () => {
	it('returns [] for an empty selection', () => {
		expect(groupAmountsByCurrency([])).toEqual([]);
	});

	it('collapses a single-currency selection into one exact subtotal', () => {
		expect(
			groupAmountsByCurrency([
				{ amount: '250.00', currency: 'USD' },
				{ amount: '250.00', currency: 'USD' }
			])
		).toEqual([{ currency: 'USD', total: 500, count: 2 }]);
	});

	it('NEVER adds across currencies — one bucket per currency', () => {
		// The bug this helper exists to prevent: EUR 100 + USD 100 rendering
		// as a single "200".
		const groups = groupAmountsByCurrency([
			{ amount: '100.00', currency: 'USD' },
			{ amount: '100.00', currency: 'EUR' }
		]);
		expect(groups).toEqual([
			{ currency: 'EUR', total: 100, count: 1 },
			{ currency: 'USD', total: 100, count: 1 }
		]);
		// And no caller can accidentally read a combined figure off it.
		expect(groups.length).toBe(2);
	});

	it('sums within a currency exactly (no float drift)', () => {
		// The classic 0.1 + 0.2 case: a float reduce yields 0.30000000000000004.
		const [group] = groupAmountsByCurrency([
			{ amount: '0.10', currency: 'GBP' },
			{ amount: '0.20', currency: 'GBP' }
		]);
		expect(group.total).toBe(0.3);
	});

	it('normalises currency codes (case + whitespace) into one bucket', () => {
		expect(
			groupAmountsByCurrency([
				{ amount: '10.00', currency: 'usd' },
				{ amount: '10.00', currency: '  USD ' }
			])
		).toEqual([{ currency: 'USD', total: 20, count: 2 }]);
	});

	it('falls back for a missing / malformed code rather than dropping the row', () => {
		// Dropping the row would understate the selection — the exact failure
		// mode the helper exists to prevent.
		expect(
			groupAmountsByCurrency(
				[
					{ amount: '5.00', currency: null },
					{ amount: '5.00' },
					{ amount: '5.00', currency: 'US' }
				],
				'ZAR'
			)
		).toEqual([{ currency: 'ZAR', total: 15, count: 3 }]);
	});

	it('defaults the fallback to USD when the caller gives none', () => {
		expect(groupAmountsByCurrency([{ amount: '1.00', currency: '' }])).toEqual([
			{ currency: 'USD', total: 1, count: 1 }
		]);
	});

	it('orders groups by currency code ascending, deterministically', () => {
		const codes = groupAmountsByCurrency([
			{ amount: '1', currency: 'ZAR' },
			{ amount: '9999', currency: 'AUD' },
			{ amount: '5', currency: 'GBP' }
		]).map((g) => g.currency);
		expect(codes).toEqual(['AUD', 'GBP', 'ZAR']);
	});

	it('counts non-numeric / null amounts as members but not as value', () => {
		// `sumMoney` skips unparseable entries; the row is still part of the
		// selection, so `count` must include it.
		expect(
			groupAmountsByCurrency([
				{ amount: '10.00', currency: 'USD' },
				{ amount: null, currency: 'USD' }
			])
		).toEqual([{ currency: 'USD', total: 10, count: 2 }]);
	});
});

describe('spansMultipleCurrencies', () => {
	it('is false for nothing selected and for one currency', () => {
		expect(spansMultipleCurrencies([])).toBe(false);
		expect(spansMultipleCurrencies(groupAmountsByCurrency([{ amount: '1', currency: 'USD' }]))).toBe(
			false
		);
	});

	it('is true the moment a second currency joins the selection', () => {
		expect(
			spansMultipleCurrencies(
				groupAmountsByCurrency([
					{ amount: '1', currency: 'USD' },
					{ amount: '1', currency: 'EUR' }
				])
			)
		).toBe(true);
	});
});
