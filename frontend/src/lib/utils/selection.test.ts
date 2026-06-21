import { describe, it, expect } from 'vitest';
import { pruneSelection } from './selection';

describe('pruneSelection', () => {
	it('drops selected ids that are no longer visible', () => {
		// The bug: after a filter/refetch narrows the list, the selection keeps
		// ids that fell off — inflating the bulk-bar count and feeding invisible
		// ids into bulk delete/status/export.
		const selected = new Set(['a', 'b', 'c', 'd']);
		const visible = ['b', 'd']; // a + c fell off the list
		const pruned = pruneSelection(selected, visible);
		expect([...pruned].sort()).toEqual(['b', 'd']);
	});

	it('returns the SAME Set instance when nothing went stale (no needless write)', () => {
		// Identity preservation lets a Svelte $effect guard its reassignment on
		// `pruned !== selected`, so it never loops when reading+writing selection.
		const selected = new Set(['a', 'b']);
		const pruned = pruneSelection(selected, ['a', 'b', 'c']); // superset visible
		expect(pruned).toBe(selected);
	});

	it('returns the same instance for an empty selection', () => {
		const selected = new Set<string>();
		expect(pruneSelection(selected, ['x', 'y'])).toBe(selected);
	});

	it('prunes everything when nothing is visible', () => {
		const selected = new Set(['a', 'b']);
		const pruned = pruneSelection(selected, []);
		expect(pruned).not.toBe(selected);
		expect(pruned.size).toBe(0);
	});

	it('accepts a Set as the visible-ids argument', () => {
		const selected = new Set(['a', 'b', 'c']);
		const pruned = pruneSelection(selected, new Set(['c']));
		expect([...pruned]).toEqual(['c']);
	});

	it('keeps only the surviving ids and order-independently', () => {
		const selected = new Set(['z', 'm', 'a']);
		const pruned = pruneSelection(selected, ['a', 'z', 'q']);
		expect([...pruned].sort()).toEqual(['a', 'z']);
	});
});
