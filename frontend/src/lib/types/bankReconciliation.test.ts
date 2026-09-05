import { describe, expect, it } from 'vitest';

import {
	DISCREPANCY_METHODS,
	MATCH_STATE_TONES,
	isDiscrepancyMethod,
	isTruncated,
	needsHumanDecision,
	transactionMatchState,
	type MatchState,
	type MatchStateInput
} from './bankReconciliation';

/**
 * `transactionMatchState` is the whole judgment the bank-reconciliation UI
 * makes: it decides whether a row reads as a fact, a suggestion, or a problem.
 * Getting it wrong in the "too confident" direction is the failure that
 * matters — a fuzzy vendor-name coincidence rendered as a confirmed clearing
 * is bank reconciliation signing off on the very thing it exists to catch.
 */

function tx(overrides: Partial<MatchStateInput> = {}): MatchStateInput {
	return {
		direction: 'debit',
		matched_payment_id: 'pay-1',
		match_method: 'provider_id',
		match_confidence: 100,
		is_reconciled: true,
		...overrides
	};
}

describe('isDiscrepancyMethod', () => {
	it.each(DISCREPANCY_METHODS)('%s is a discrepancy class', (method) => {
		expect(isDiscrepancyMethod(method)).toBe(true);
	});

	it.each(['provider_id', 'amount_date', 'fuzzy_vendor', 'manual'])(
		'%s is an identity method, not a discrepancy',
		(method) => {
			expect(isDiscrepancyMethod(method)).toBe(false);
		}
	);

	it('treats null / undefined / unknown as not-a-discrepancy', () => {
		expect(isDiscrepancyMethod(null)).toBe(false);
		expect(isDiscrepancyMethod(undefined)).toBe(false);
		expect(isDiscrepancyMethod('something_new')).toBe(false);
	});

	it('carries exactly the backend classes (UNRECONCILED_MATCH_METHODS)', () => {
		expect([...DISCREPANCY_METHODS].sort()).toEqual([
			'amount_mismatch',
			'currency_mismatch',
			'status_conflict'
		]);
	});
});

describe('transactionMatchState', () => {
	it('a credit is never a clearing, whatever else the row says', () => {
		// A payment is money we SENT. The auto-matcher skips credits entirely and
		// `/resolve` 409s on one, so a credit must never render as a match — even
		// one carrying a (stale) payment link.
		expect(transactionMatchState(tx({ direction: 'credit' }))).toBe('credit');
		expect(
			transactionMatchState(
				tx({ direction: 'credit', matched_payment_id: 'pay-1', is_reconciled: true })
			)
		).toBe('credit');
	});

	it('a debit with no payment behind it is unmatched', () => {
		expect(
			transactionMatchState(
				tx({ matched_payment_id: null, match_method: null, match_confidence: null })
			)
		).toBe('unmatched');
	});

	it.each(DISCREPANCY_METHODS)('a %s line is a discrepancy, not a match', (method) => {
		// Every discrepancy class arrives at confidence 100 — the IDENTITY is
		// certain. Reading confidence alone would paint it green.
		expect(
			transactionMatchState(
				tx({ match_method: method, match_confidence: 100, is_reconciled: false })
			)
		).toBe('discrepancy');
	});

	it('trusts is_reconciled over an unrecognised match method', () => {
		// A method this frontend has never seen must degrade to "a human needs to
		// look", never to a clean tick. `is_reconciled` is the backend's own
		// predicate, so it wins.
		expect(
			transactionMatchState(
				tx({ match_method: 'some_future_class', match_confidence: 100, is_reconciled: false })
			)
		).toBe('discrepancy');
	});

	it('scores identity strength on a reconciled line', () => {
		expect(transactionMatchState(tx({ match_confidence: 100 }))).toBe('confirmed');
		expect(transactionMatchState(tx({ match_method: 'manual', match_confidence: 100 }))).toBe(
			'confirmed'
		);
		expect(transactionMatchState(tx({ match_method: 'amount_date', match_confidence: 80 }))).toBe(
			'probable'
		);
		expect(transactionMatchState(tx({ match_method: 'amount_date', match_confidence: 99 }))).toBe(
			'probable'
		);
	});

	it('a fuzzy vendor-name hit (50–79) is only ever a suggestion', () => {
		for (const confidence of [50, 60, 70, 79]) {
			expect(
				transactionMatchState(tx({ match_method: 'fuzzy_vendor', match_confidence: confidence }))
			).toBe('suggested');
		}
	});

	it('a linked line carrying NO confidence is a suggestion, not a certainty', () => {
		// Absence of evidence is not evidence of a match.
		expect(transactionMatchState(tx({ match_confidence: null }))).toBe('suggested');
	});
});

describe('needsHumanDecision', () => {
	it('claims the three states a reviewer still owes an answer on', () => {
		expect(needsHumanDecision('unmatched')).toBe(true);
		expect(needsHumanDecision('discrepancy')).toBe(true);
		expect(needsHumanDecision('suggested')).toBe(true);
	});

	it('leaves settled states alone', () => {
		expect(needsHumanDecision('confirmed')).toBe(false);
		expect(needsHumanDecision('probable')).toBe(false);
		// A credit is not ours to reconcile.
		expect(needsHumanDecision('credit')).toBe(false);
	});
});

describe('MATCH_STATE_TONES', () => {
	it('names a tone for every state (no state can fall through untinted)', () => {
		const states: MatchState[] = [
			'credit',
			'unmatched',
			'discrepancy',
			'confirmed',
			'probable',
			'suggested'
		];
		for (const state of states) {
			expect(MATCH_STATE_TONES[state], `${state} has no tone`).toBeTruthy();
		}
	});

	it('reserves green for a state that is actually settled', () => {
		// `probable` is an amount+date coincidence in a ±5-day window, not a
		// confirmation — green would read as "signed off".
		expect(MATCH_STATE_TONES.confirmed).toBe('success');
		expect(MATCH_STATE_TONES.probable).not.toBe('success');
		expect(MATCH_STATE_TONES.suggested).not.toBe('success');
		expect(MATCH_STATE_TONES.discrepancy).toBe('danger');
	});
});

describe('isTruncated', () => {
	it('flags a bucket whose rows were capped below its whole-set count', () => {
		expect(isTruncated(500, 1200)).toBe(true);
	});

	it('is false when every row is on screen', () => {
		expect(isTruncated(12, 12)).toBe(false);
		expect(isTruncated(0, 0)).toBe(false);
	});
});
