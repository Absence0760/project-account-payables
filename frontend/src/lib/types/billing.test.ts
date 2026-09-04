import { describe, expect, it } from 'vitest';

import { rebateMeterGroups, type BillingUsage } from './billing';

function usage(extra: Record<string, string> = {}): BillingUsage {
	return { extractions: '3', extractions_platform: '2', ...extra };
}

describe('rebateMeterGroups', () => {
	it('reads one group per currency meter', () => {
		expect(
			rebateMeterGroups(
				usage({ 'card_rebate_total.USD': '15.00', 'card_rebate_total.EUR': '7.00' })
			)
		).toEqual([
			{ currency: 'EUR', total: '7.00' },
			{ currency: 'USD', total: '15.00' }
		]);
	});

	it('is empty when the org accrued no rebates', () => {
		// The backend emits no rebate key at all rather than a zero in an
		// unstated currency, so the page renders nothing instead of a `$0.00`
		// whose currency it invented.
		expect(rebateMeterGroups(usage())).toEqual([]);
		expect(rebateMeterGroups(null)).toEqual([]);
		expect(rebateMeterGroups(undefined)).toEqual([]);
	});

	it('never returns a bare cross-currency total', () => {
		// `card_rebate_total` with no currency suffix is the pre-fix shape: a
		// figure in no currency. It must not be rendered under a guessed code.
		expect(rebateMeterGroups(usage({ card_rebate_total: '22.00' }))).toEqual([]);
	});

	it('ignores a malformed currency suffix rather than fabricating a code', () => {
		expect(
			rebateMeterGroups(
				usage({
					'card_rebate_total.': '1.00',
					'card_rebate_total.US': '2.00',
					'card_rebate_total.DOLLARS': '3.00',
					'card_rebate_total.gbp': '4.00'
				})
			)
		).toEqual([{ currency: 'GBP', total: '4.00' }]);
	});

	it('leaves other meters alone', () => {
		const groups = rebateMeterGroups(
			usage({ extractions_lambda: '9', 'card_rebate_total.USD': '1.00' })
		);
		expect(groups).toEqual([{ currency: 'USD', total: '1.00' }]);
	});

	it('keeps the amount an exact string', () => {
		// Money never round-trips through a float on the way to the screen.
		const [g] = rebateMeterGroups(usage({ 'card_rebate_total.JPY': '12345678901234.56' }));
		expect(g.total).toBe('12345678901234.56');
	});
});
