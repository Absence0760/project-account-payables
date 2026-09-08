import { describe, it, expect } from 'vitest';
import {
	SCREENING_STATUS_LABEL_KEYS,
	VENDOR_SOURCE_LABEL_KEYS,
	VENDOR_STATUSES,
	VENDOR_STATUS_LABEL_KEYS,
	VENDOR_STATUS_TONES,
	screeningStatusLabelKey,
	vendorSourceLabelKey,
	vendorStatusLabelKey,
	type ScreeningStatus
} from './vendor';
import { en } from '$lib/i18n/locales/en';

/**
 * Map-completeness guard for the sanctions-screening label map.
 *
 * `Record<ScreeningStatus, MessageKey>` already fails `pnpm check` when a
 * status joins the union with no key — but a key naming nothing in the
 * catalogue typechecks fine and renders the raw key string in the pill
 * (`m()` falls back key → raw). This is the runtime half.
 */
const SCREENING_STATUSES: ScreeningStatus[] = ['unscreened', 'clear', 'review', 'match'];

describe('SCREENING_STATUS_LABEL_KEYS', () => {
	it('names a real catalogue key for every screening status', () => {
		for (const status of SCREENING_STATUSES) {
			const key = SCREENING_STATUS_LABEL_KEYS[status];
			expect(key, `${status} has no label key`).toBeTruthy();
			expect(Object.keys(en), `${status} → "${key}" is not in the catalogue`).toContain(key);
		}
	});

	it('never uses the raw enum value as its own label', () => {
		for (const status of SCREENING_STATUSES) {
			expect(en[SCREENING_STATUS_LABEL_KEYS[status]]).not.toBe(status);
		}
	});

	it('screeningStatusLabelKey resolves a known status and returns null otherwise', () => {
		for (const status of SCREENING_STATUSES) {
			expect(screeningStatusLabelKey(status)).toBe(SCREENING_STATUS_LABEL_KEYS[status]);
		}
		// The pill renders the raw value — visible and searchable — rather than
		// blank, so a verdict a new provider introduces degrades gracefully.
		expect(screeningStatusLabelKey('probable_match')).toBeNull();
	});

	it('carries the pill labels ScreeningBadge renders beside the verdict', () => {
		// Both live in the same pill row as the verdict above; an English
		// literal there is the hybrid this conversion exists to remove.
		expect(Object.keys(en)).toContain('vendors.screening.blocked');
		expect(Object.keys(en)).toContain('vendors.screening.adverseMedia');
		expect(Object.keys(en)).toContain('vendors.screening.adverseMediaTitle');
	});
});

describe('VENDOR_STATUS_LABEL_KEYS', () => {
	it('names a real catalogue key for every lifecycle status', () => {
		for (const status of VENDOR_STATUSES) {
			const key = VENDOR_STATUS_LABEL_KEYS[status];
			expect(key, `${status} has no label key`).toBeTruthy();
			expect(Object.keys(en), `${status} → "${key}" is not in the catalogue`).toContain(key);
			expect(en[key]).not.toBe(status);
		}
	});

	it('labels every status the tone map tints', () => {
		// The two are read one after the other on the same badge — a status
		// with a tone but no label renders a coloured pill showing a raw enum
		// value. They live in this module together for exactly that reason.
		for (const status of Object.keys(VENDOR_STATUS_TONES)) {
			expect(vendorStatusLabelKey(status), `${status} has a tone but no label`).toBeTruthy();
		}
	});

	it('resolves a known status and returns null otherwise', () => {
		for (const status of VENDOR_STATUSES) {
			expect(vendorStatusLabelKey(status)).toBe(VENDOR_STATUS_LABEL_KEYS[status]);
		}
		// `Vendor.status` is untyped on the wire, so the row renders the raw
		// value rather than a blank badge for one this build does not know.
		expect(vendorStatusLabelKey('archived')).toBeNull();
	});
});

describe('VENDOR_SOURCE_LABEL_KEYS', () => {
	it('names a real catalogue key for every vendor source', () => {
		for (const key of Object.values(VENDOR_SOURCE_LABEL_KEYS)) {
			expect(Object.keys(en), `"${key}" is not in the catalogue`).toContain(key);
		}
		expect(vendorSourceLabelKey('erp_sync')).toBe('vendors.source.erpSync');
		expect(vendorSourceLabelKey('portal_self_service')).toBeNull();
	});
});
