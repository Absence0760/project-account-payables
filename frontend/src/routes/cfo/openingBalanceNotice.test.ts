import { describe, expect, it } from 'vitest';
import { openingBalanceSkipKey } from './openingBalanceNotice';

// `/cfo` is where a finance leader reads the projected cash curve, so "the
// number you're looking at does NOT start from your bank" has to be visible
// there and not only in the copilot's chat narration.

describe('openingBalanceSkipKey', () => {
	it('says nothing when the chain used what it found', () => {
		// A provider balance that WAS used, a plain settings figure, an
		// explicit override — all leave the field null, and the card must not
		// grow a standing warning.
		expect(openingBalanceSkipKey(null)).toBeNull();
		expect(openingBalanceSkipKey(undefined)).toBeNull();
		expect(openingBalanceSkipKey('')).toBeNull();
		expect(openingBalanceSkipKey('   ')).toBeNull();
	});

	it('explains the currency refusal specifically', () => {
		expect(openingBalanceSkipKey('currency_mismatch')).toBe(
			'cfo.position.providerSkippedCurrency'
		);
	});

	it('still speaks for a reason code it does not recognise', () => {
		// The backend can add a reason before the frontend learns its wording.
		// Falling back to `null` would leave the CFO reading a projection that
		// silently isn't seeded from their bank — the exact bug this closes.
		expect(openingBalanceSkipKey('bank_link_expired')).toBe('cfo.position.providerSkipped');
		expect(openingBalanceSkipKey('something_new')).toBe('cfo.position.providerSkipped');
	});

	it('tolerates a non-string payload', () => {
		expect(openingBalanceSkipKey(42 as unknown as string)).toBeNull();
	});
});
