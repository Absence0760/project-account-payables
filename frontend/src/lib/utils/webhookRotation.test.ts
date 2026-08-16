import { describe, expect, it } from 'vitest';
import {
	OVERLAP_CHOICES,
	OVERLAP_DEFAULT_MINUTES,
	OVERLAP_MAX_MINUTES,
	OVERLAP_MIN_MINUTES,
	isOverlapLive,
	isValidOverlapMinutes
} from './webhookRotation';

describe('isValidOverlapMinutes', () => {
	it('accepts the documented bounds and the backend default', () => {
		expect(isValidOverlapMinutes(OVERLAP_MIN_MINUTES)).toBe(true);
		expect(isValidOverlapMinutes(OVERLAP_MAX_MINUTES)).toBe(true);
		expect(isValidOverlapMinutes(OVERLAP_DEFAULT_MINUTES)).toBe(true);
	});

	it('rejects out-of-range values rather than clamping them', () => {
		// The backend 422s these; the picker must never send one.
		expect(isValidOverlapMinutes(-1)).toBe(false);
		expect(isValidOverlapMinutes(OVERLAP_MAX_MINUTES + 1)).toBe(false);
	});

	it('rejects non-integer / non-finite minutes', () => {
		expect(isValidOverlapMinutes(60.5)).toBe(false);
		expect(isValidOverlapMinutes(Number.NaN)).toBe(false);
		expect(isValidOverlapMinutes(Number.POSITIVE_INFINITY)).toBe(false);
	});
});

describe('OVERLAP_CHOICES', () => {
	it('only offers windows the backend accepts', () => {
		for (const choice of OVERLAP_CHOICES) {
			expect(isValidOverlapMinutes(choice.minutes)).toBe(true);
		}
	});

	it('leads with the hard cutover and includes the default', () => {
		expect(OVERLAP_CHOICES[0].minutes).toBe(OVERLAP_MIN_MINUTES);
		expect(OVERLAP_CHOICES.map((c) => c.minutes)).toContain(OVERLAP_DEFAULT_MINUTES);
	});

	it('is ordered shortest-window first', () => {
		const minutes = OVERLAP_CHOICES.map((c) => c.minutes);
		expect([...minutes].sort((a, b) => a - b)).toEqual(minutes);
	});

	it('has no duplicate windows', () => {
		const minutes = OVERLAP_CHOICES.map((c) => c.minutes);
		expect(new Set(minutes).size).toBe(minutes.length);
	});
});

describe('isOverlapLive', () => {
	const now = Date.parse('2026-08-15T12:00:00Z');

	it('is live while the expiry is in the future', () => {
		expect(isOverlapLive('2026-08-15T13:00:00Z', now)).toBe(true);
	});

	it('is not live once the expiry has passed', () => {
		expect(isOverlapLive('2026-08-15T11:59:59Z', now)).toBe(false);
	});

	it('treats the exact expiry instant as no longer live', () => {
		// Matches the backend's strict `expires > now` — at the boundary the
		// retiring secret has stopped signing.
		expect(isOverlapLive('2026-08-15T12:00:00Z', now)).toBe(false);
	});

	it('reads a hard cutover (no window) as not live', () => {
		expect(isOverlapLive(null, now)).toBe(false);
		expect(isOverlapLive(undefined, now)).toBe(false);
		expect(isOverlapLive('', now)).toBe(false);
	});

	it('fails closed on an unparseable timestamp', () => {
		// Claiming an unknown window is still open would tell an admin they have
		// time they may not have.
		expect(isOverlapLive('not-a-date', now)).toBe(false);
	});

	it('honours the offset in a non-UTC timestamp', () => {
		// 13:00+02:00 is 11:00Z — already past `now`, despite the wall clock
		// reading later than 12:00.
		expect(isOverlapLive('2026-08-15T13:00:00+02:00', now)).toBe(false);
		expect(isOverlapLive('2026-08-15T15:00:00+02:00', now)).toBe(true);
	});
});
