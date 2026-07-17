import { describe, it, expect } from 'vitest';
import { appendUnique } from './pagination';

const row = (id: string, extra: Record<string, unknown> = {}) => ({ id, ...extra });

describe('appendUnique', () => {
	it('appends a disjoint next page unchanged', () => {
		const page1 = [row('a'), row('b')];
		const page2 = [row('c'), row('d')];
		expect(appendUnique(page1, page2).map((r) => r.id)).toEqual(['a', 'b', 'c', 'd']);
	});

	it('drops re-surfaced rows across an offset-pagination overlap window', () => {
		// The bug: a row inserted between fetches shifts the offset window, so
		// page 2 re-serves the tail of page 1. Appending it raw duplicates the
		// id and crashes the keyed {#each ... (id)} with each_key_duplicate.
		const page1 = [row('a'), row('b'), row('c')];
		const page2 = [row('b'), row('c'), row('d')]; // b + c re-surfaced
		expect(appendUnique(page1, page2).map((r) => r.id)).toEqual(['a', 'b', 'c', 'd']);
	});

	it('flagship collision: newest-first list where an insert re-surfaces the whole page boundary', () => {
		// Notifications: newest-first ordering + a notification arriving between
		// the page-1 fetch (or the 60s poll refresh) and Load More pushes every
		// row down one slot, so page 2 starts with page 1's last row.
		const page1 = [row('n5'), row('n4'), row('n3')];
		// A new n6 arrived; page 2 (offset 3) now re-serves n3.
		const page2 = [row('n3'), row('n2'), row('n1')];
		const merged = appendUnique(page1, page2);
		expect(merged.map((r) => r.id)).toEqual(['n5', 'n4', 'n3', 'n2', 'n1']);
		// No duplicate keys — the keyed {#each} invariant.
		expect(new Set(merged.map((r) => r.id)).size).toBe(merged.length);
	});

	it('handles an empty incoming page (no-op append)', () => {
		const existing = [row('a'), row('b')];
		const merged = appendUnique(existing, []);
		expect(merged.map((r) => r.id)).toEqual(['a', 'b']);
	});

	it('handles an empty existing list (first page via append path)', () => {
		expect(appendUnique([], [row('a'), row('b')]).map((r) => r.id)).toEqual(['a', 'b']);
	});

	it('handles both sides empty', () => {
		expect(appendUnique([], [])).toEqual([]);
	});

	it('preserves order: existing rows first, then incoming new rows in server order', () => {
		const existing = [row('c'), row('a')];
		const incoming = [row('z'), row('a'), row('m')];
		expect(appendUnique(existing, incoming).map((r) => r.id)).toEqual(['c', 'a', 'z', 'm']);
	});

	it('existing row wins: a re-surfaced duplicate never replaces the loaded row', () => {
		// Matches the original invoice-store inline guard: the already-rendered
		// row is kept even if the server re-serves a fresher copy of it.
		const existing = [{ id: 'a', status: 'new' }];
		const incoming = [
			{ id: 'a', status: 'approved' },
			{ id: 'b', status: 'new' },
		];
		const merged = appendUnique(existing, incoming);
		expect(merged).toHaveLength(2);
		expect(merged[0]).toBe(existing[0]); // same object reference, untouched
		expect(merged[0].status).toBe('new');
	});

	it('dedups within a fully-duplicated retry page (idempotent re-append)', () => {
		const page1 = [row('a'), row('b')];
		// e.g. a double-clicked Load More that re-fetched the same page.
		const merged = appendUnique(page1, [row('a'), row('b')]);
		expect(merged.map((r) => r.id)).toEqual(['a', 'b']);
	});

	it('does not mutate its inputs', () => {
		const existing = [row('a')];
		const incoming = [row('a'), row('b')];
		appendUnique(existing, incoming);
		expect(existing.map((r) => r.id)).toEqual(['a']);
		expect(incoming.map((r) => r.id)).toEqual(['a', 'b']);
	});
});
