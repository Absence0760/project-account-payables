import { describe, it, expect } from 'vitest';
import {
	POSITIVE_PAY_FILE_TYPES,
	POSITIVE_PAY_FILE_TYPE_LABEL_KEYS,
	POSITIVE_PAY_STATUSES,
	POSITIVE_PAY_STATUS_LABEL_KEYS,
	positivePayFileTypeLabelKey,
	positivePayStatusLabelKey
} from './positivePay';
import { en } from '$lib/i18n/locales/en';

/**
 * Map-completeness guard for the Positive Pay label maps.
 *
 * `Record<…, MessageKey>` already fails `pnpm check` when a value joins a
 * union with no key — but a key naming nothing in the catalogue typechecks
 * fine and renders the raw key string (`m()` falls back key → raw). This is
 * the runtime half.
 */
describe('POSITIVE_PAY_STATUS_LABEL_KEYS', () => {
	it('names a real catalogue key for every status', () => {
		for (const status of POSITIVE_PAY_STATUSES) {
			const key = POSITIVE_PAY_STATUS_LABEL_KEYS[status];
			expect(key, `${status} has no label key`).toBeTruthy();
			expect(Object.keys(en), `${status} → "${key}" is not in the catalogue`).toContain(key);
		}
	});

	it('resolves a known status and returns null otherwise', () => {
		for (const status of POSITIVE_PAY_STATUSES) {
			expect(positivePayStatusLabelKey(status)).toBe(POSITIVE_PAY_STATUS_LABEL_KEYS[status]);
		}
		expect(positivePayStatusLabelKey('acknowledged')).toBeNull();
	});
});

describe('POSITIVE_PAY_FILE_TYPE_LABEL_KEYS', () => {
	it('names a real catalogue key for every file type', () => {
		for (const fileType of POSITIVE_PAY_FILE_TYPES) {
			const key = POSITIVE_PAY_FILE_TYPE_LABEL_KEYS[fileType];
			expect(key, `${fileType} has no label key`).toBeTruthy();
			expect(Object.keys(en), `${fileType} → "${key}" is not in the catalogue`).toContain(key);
		}
	});

	it('resolves a known file type and returns null otherwise', () => {
		// The label is interpolated into `positivePay.fileLabel`, so an
		// unrecognised type must degrade to its raw value rather than a blank
		// row label.
		expect(positivePayFileTypeLabelKey('check_issue')).toBe('positivePay.fileType.checkIssue');
		expect(positivePayFileTypeLabelKey('wire_authorization')).toBeNull();
	});
});
