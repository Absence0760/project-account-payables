import { describe, it, expect } from 'vitest';
import {
	headersToRows,
	headersIdentity,
	rowsMatchHeaders,
	rowsToHeaders,
	type HeaderRow
} from './webhookHeaders';

describe('rowsToHeaders', () => {
	it('drops a blank-key row rather than persisting an empty header name', () => {
		expect(rowsToHeaders([['X-Token', 'abc'], ['', '']])).toEqual({ 'X-Token': 'abc' });
	});

	it('trims the key but leaves the value untouched', () => {
		expect(rowsToHeaders([['  X-Token  ', '  abc  ']])).toEqual({ 'X-Token': '  abc  ' });
	});

	it('last row wins on a duplicate key, like an object literal', () => {
		expect(rowsToHeaders([['X', '1'], ['X', '2']])).toEqual({ X: '2' });
	});
});

describe('rowsMatchHeaders — the re-seed decision', () => {
	it('a blank row being typed is NOT a mismatch, so the editor keeps it', () => {
		// The bug: "+ Add header" appended ['', ''], the projection dropped it,
		// the config came back identical, and a `$derived` re-derived the row
		// away — so no blank row ever appeared and a header could not be added.
		const rows: HeaderRow[] = [['X-Token', 'abc'], ['', '']];
		expect(rowsMatchHeaders(rows, { 'X-Token': 'abc' })).toBe(true);
	});

	it('clearing an existing header NAME keeps its value on screen', () => {
		// Same round trip: patchHeaderKey(idx, '') dropped the key, and the row —
		// value included — vanished mid-edit.
		const rows: HeaderRow[] = [['', 'abc']];
		expect(rowsMatchHeaders(rows, {})).toBe(true);
	});

	it('key order alone is not a mismatch', () => {
		// The persisted order is whatever the JSON round trip produced; comparing
		// it raw would re-seed on every keystroke and discard the row being typed.
		const rows: HeaderRow[] = [['B', '2'], ['A', '1']];
		expect(rowsMatchHeaders(rows, { A: '1', B: '2' })).toBe(true);
	});

	it('a genuinely different config IS a mismatch, so the editor re-seeds', () => {
		const rows: HeaderRow[] = [['X-Token', 'abc']];
		expect(rowsMatchHeaders(rows, { 'X-Token': 'CHANGED' })).toBe(false);
		expect(rowsMatchHeaders(rows, { Other: 'abc' })).toBe(false);
		expect(rowsMatchHeaders(rows, {})).toBe(false);
	});

	it('empty rows match an absent headers object', () => {
		expect(rowsMatchHeaders([], undefined)).toBe(true);
		expect(rowsMatchHeaders([], null)).toBe(true);
		expect(rowsMatchHeaders([], {})).toBe(true);
	});
});

describe('headersToRows / headersIdentity', () => {
	it('round-trips a populated object', () => {
		expect(rowsToHeaders(headersToRows({ A: '1', B: '2' }))).toEqual({ A: '1', B: '2' });
	});

	it('treats absent and empty the same', () => {
		expect(headersToRows(undefined)).toEqual([]);
		expect(headersIdentity(undefined)).toBe(headersIdentity({}));
	});
});
