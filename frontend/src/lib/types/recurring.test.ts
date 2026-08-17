import { describe, it, expect } from 'vitest';
import { GENERATION_SKIP_REASONS, skipReasonKey } from './recurring';
import { en } from '$lib/i18n/locales/en';

describe('skipReasonKey', () => {
	it('names every reason code the backend sweep can emit', () => {
		for (const code of GENERATION_SKIP_REASONS) {
			const key = skipReasonKey(code);
			expect(key, `no message key for reason "${code}"`).toBeTruthy();
			// The key must actually exist in the catalogue, or `m()` would fall
			// back to rendering the raw key string in the badge tooltip.
			expect(Object.keys(en)).toContain(key as string);
		}
	});

	it('returns null for a code this frontend does not know', () => {
		// The caller renders the raw code — visible and searchable — rather than
		// a blank tooltip, so a newly-added backend reason degrades gracefully
		// until this map catches up (the test above is what makes it catch up).
		expect(skipReasonKey('missing_moon_phase')).toBeNull();
	});
});
