import { describe, expect, it } from 'vitest';

import {
	hasPartialRealisedSet,
	partialRealisedCount,
	type RealisedExclusionCounts
} from './discountPartialSet';

function counts(captured: number, missed: number): RealisedExclusionCounts {
	return { excluded_captured_count: captured, excluded_missed_count: missed };
}

describe('partialRealisedCount', () => {
	it('is zero when nothing was excluded', () => {
		expect(partialRealisedCount(counts(0, 0))).toBe(0);
	});

	it('counts an exclusion in either bucket on its own', () => {
		// The two buckets move independently — the backend filters `captured` and
		// `missed` separately, so a tenant can have foreign-currency captures and
		// no foreign-currency misses (or the mirror image).
		expect(partialRealisedCount(counts(2, 0))).toBe(2);
		expect(partialRealisedCount(counts(0, 3))).toBe(3);
	});

	it('sums both buckets', () => {
		expect(partialRealisedCount(counts(2, 3))).toBe(5);
	});

	it('treats a missing dashboard as nothing to disclose', () => {
		// The page renders before the first load resolves; an unloaded dashboard
		// must not claim a partial set.
		expect(partialRealisedCount(null)).toBe(0);
		expect(partialRealisedCount(undefined)).toBe(0);
	});

	it('never lets a malformed count cancel out a real exclusion', () => {
		// The counts come off the wire. A negative in one bucket must not
		// subtract from a genuine exclusion in the other and hide the banner.
		expect(partialRealisedCount(counts(-4, 2))).toBe(2);
		expect(hasPartialRealisedSet(counts(-4, 2))).toBe(true);
	});

	it('ignores non-numeric and non-finite counts', () => {
		const shapes = [
			{ excluded_captured_count: NaN, excluded_missed_count: 2 },
			{ excluded_captured_count: Infinity, excluded_missed_count: 0 },
			{ excluded_captured_count: undefined, excluded_missed_count: 2 },
			{ excluded_captured_count: null, excluded_missed_count: 2 }
		] as unknown as RealisedExclusionCounts[];
		expect(shapes.map(partialRealisedCount)).toEqual([2, 0, 2, 2]);
	});

	it('floors a fractional count rather than rendering one', () => {
		expect(partialRealisedCount(counts(2.7, 0))).toBe(2);
	});
});

describe('hasPartialRealisedSet', () => {
	it('is false only when both buckets are clean', () => {
		expect(hasPartialRealisedSet(counts(0, 0))).toBe(false);
		expect(hasPartialRealisedSet(null)).toBe(false);
	});

	it('is true as soon as one realised offer is excluded', () => {
		// One excluded offer is enough: `captured_amount` / `missed_amount` are
		// then a subset, and showing a subset as the whole is the defect the
		// backend fix removed.
		expect(hasPartialRealisedSet(counts(1, 0))).toBe(true);
		expect(hasPartialRealisedSet(counts(0, 1))).toBe(true);
		expect(hasPartialRealisedSet(counts(4, 9))).toBe(true);
	});

	it('agrees with partialRealisedCount on every shape', () => {
		for (const captured of [0, 1, 5]) {
			for (const missed of [0, 1, 5]) {
				const dashboard = counts(captured, missed);
				expect(hasPartialRealisedSet(dashboard)).toBe(partialRealisedCount(dashboard) > 0);
			}
		}
	});
});
