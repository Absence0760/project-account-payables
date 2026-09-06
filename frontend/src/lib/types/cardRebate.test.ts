import { describe, expect, it } from 'vitest';
import {
	formatRebateRate,
	nextRebateTransition,
	rebateAmountCurrency,
	rebateTone,
	REBATE_STATUSES
} from './cardRebate';

describe('rebateAmountCurrency', () => {
	it('names the reporting currency when the list is provably homogeneous', () => {
		// `excluded_rebate_count` is computed over the SAME filter as `items`, so
		// zero exclusions proves every listed row is denominated in `currency`.
		expect(rebateAmountCurrency({ currency: 'GBP', excluded_rebate_count: 0 })).toBe('GBP');
	});

	it('refuses to name one when at least one row is denominated elsewhere', () => {
		// Nothing on the wire says WHICH row — `RebateResponse` carries no
		// currency, and a rebate's currency is knowable only through its card.
		// Stamping the reporting code onto every row would put a symbol on a
		// figure that is not in it.
		expect(rebateAmountCurrency({ currency: 'USD', excluded_rebate_count: 1 })).toBeNull();
	});

	it('treats a missing count as zero rather than crashing', () => {
		expect(
			rebateAmountCurrency({ currency: 'EUR' } as unknown as Parameters<
				typeof rebateAmountCurrency
			>[0])
		).toBe('EUR');
	});
});

describe('nextRebateTransition', () => {
	it('offers exactly the step the backend will accept', () => {
		expect(nextRebateTransition('pending')).toBe('confirm');
		expect(nextRebateTransition('confirmed')).toBe('mark-paid');
	});

	it('offers nothing at the terminal status — there is no skip and no reversal', () => {
		expect(nextRebateTransition('paid_out')).toBeNull();
	});

	it('offers nothing for a status the backend never persists', () => {
		expect(nextRebateTransition('cancelled')).toBeNull();
		expect(nextRebateTransition('')).toBeNull();
	});
});

describe('rebateTone', () => {
	it('tints every status the lifecycle defines', () => {
		// `pending` is warning, not success: the dashboard's realized headline
		// deliberately excludes it, so it must not read as money in the bank.
		expect(rebateTone('pending')).toBe('warning');
		expect(rebateTone('confirmed')).toBe('accent');
		expect(rebateTone('paid_out')).toBe('success');
		for (const s of REBATE_STATUSES) expect(rebateTone(s)).not.toBe('neutral');
	});

	it('falls back to a flat pill for an unknown status rather than rendering untinted', () => {
		expect(rebateTone('something_new')).toBe('neutral');
	});
});

describe('formatRebateRate', () => {
	it('renders a ratio as the percentage the rate was negotiated in', () => {
		expect(formatRebateRate(0.0125)).toBe('1.25%');
		expect(formatRebateRate('0.01')).toBe('1.00%');
	});

	it('renders the placeholder rather than NaN% for absent / unreadable input', () => {
		expect(formatRebateRate(null)).toBe('—');
		expect(formatRebateRate(undefined)).toBe('—');
		expect(formatRebateRate('')).toBe('—');
		expect(formatRebateRate('not-a-rate')).toBe('—');
		expect(formatRebateRate(Number.POSITIVE_INFINITY)).toBe('—');
	});
});
