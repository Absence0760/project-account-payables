import { describe, expect, it } from 'vitest';
import {
	VENDOR_PICKER_PAGE_SIZE,
	hasMoreVendors,
	nextActiveIndex,
	resolveEnterSelection,
	revertedQuery,
	vendorOptionLabel,
	vendorPickerCount,
	vendorSearchDelay,
	type VendorPickerOption
} from './vendorPicker';

const opt = (id: string, name: string, code?: string | null): VendorPickerOption => ({
	id,
	name,
	code
});

describe('vendorSearchDelay', () => {
	it('fires immediately when the box is cleared', () => {
		// Clearing is a request for the unfiltered list back; waiting 280ms to
		// give it is the one case where the debounce costs more than it saves.
		expect(vendorSearchDelay('')).toBe(0);
		expect(vendorSearchDelay('   ')).toBe(0);
	});

	it('debounces real typing', () => {
		expect(vendorSearchDelay('ac')).toBe(280);
	});
});

describe('vendorOptionLabel', () => {
	it('appends the vendor code when there is one', () => {
		expect(vendorOptionLabel(opt('1', 'Acme Corp', 'V-001'))).toBe('Acme Corp (V-001)');
	});

	it('omits an absent, null or blank code rather than rendering empty parens', () => {
		expect(vendorOptionLabel(opt('1', 'Acme Corp'))).toBe('Acme Corp');
		expect(vendorOptionLabel(opt('1', 'Acme Corp', null))).toBe('Acme Corp');
		expect(vendorOptionLabel(opt('1', 'Acme Corp', '   '))).toBe('Acme Corp');
	});
});

describe('vendorPickerCount', () => {
	it('reports loading only while nothing is on screen yet', () => {
		// "We have not looked" and "there is nothing" are different answers, and
		// reading one as the other is how a truncated list reads as an empty
		// tenant. Once a page has landed, a refresh keeps showing the count.
		expect(vendorPickerCount(0, 0, true)).toEqual({ kind: 'loading' });
		expect(vendorPickerCount(25, 137, true)).toEqual({ kind: 'partial', shown: 25, total: 137 });
	});

	it('reports an empty match set', () => {
		expect(vendorPickerCount(0, 0, false)).toEqual({ kind: 'none' });
	});

	it('reports a complete match set', () => {
		expect(vendorPickerCount(12, 12, false)).toEqual({ kind: 'all', total: 12 });
	});

	it('reports a partial match set — the whole reason this module exists', () => {
		expect(vendorPickerCount(25, 137, false)).toEqual({
			kind: 'partial',
			shown: 25,
			total: 137
		});
	});

	it('treats shown > total as complete, never as a negative remainder', () => {
		// The set can shrink between the page fetch and the `total` it came with.
		// "-2 more" is worse than a slightly stale "all 23".
		expect(vendorPickerCount(25, 23, false)).toEqual({ kind: 'all', total: 23 });
	});
});

describe('hasMoreVendors', () => {
	it('is true exactly when the loaded page is a subset', () => {
		expect(hasMoreVendors(25, 137)).toBe(true);
		expect(hasMoreVendors(12, 12)).toBe(false);
		expect(hasMoreVendors(0, 0)).toBe(false);
		expect(hasMoreVendors(25, 23)).toBe(false);
	});
});

describe('nextActiveIndex', () => {
	it('ignores keys that are not navigation', () => {
		expect(nextActiveIndex('a', 0, 5)).toBeNull();
		expect(nextActiveIndex('Enter', 0, 5)).toBeNull();
		expect(nextActiveIndex('Escape', 0, 5)).toBeNull();
		expect(nextActiveIndex('Tab', 0, 5)).toBeNull();
	});

	it('lands on the first option from nothing-active on ArrowDown', () => {
		expect(nextActiveIndex('ArrowDown', -1, 5)).toBe(0);
	});

	it('lands on the last option from nothing-active on ArrowUp', () => {
		expect(nextActiveIndex('ArrowUp', -1, 5)).toBe(4);
	});

	it('wraps in both directions', () => {
		expect(nextActiveIndex('ArrowDown', 4, 5)).toBe(0);
		expect(nextActiveIndex('ArrowUp', 0, 5)).toBe(4);
	});

	it('steps through the middle of the list', () => {
		expect(nextActiveIndex('ArrowDown', 1, 5)).toBe(2);
		expect(nextActiveIndex('ArrowUp', 3, 5)).toBe(2);
	});

	it('honours Home and End', () => {
		expect(nextActiveIndex('Home', 3, 5)).toBe(0);
		expect(nextActiveIndex('End', 1, 5)).toBe(4);
	});

	it('deactivates rather than pointing past the end of an empty list', () => {
		for (const key of ['ArrowDown', 'ArrowUp', 'Home', 'End']) {
			expect(nextActiveIndex(key, -1, 0)).toBe(-1);
		}
	});
});

describe('resolveEnterSelection', () => {
	const three = [opt('a', 'Alpha'), opt('b', 'Bravo'), opt('c', 'Charlie')];

	it('commits the arrowed-to option', () => {
		expect(resolveEnterSelection(1, three)).toEqual(three[1]);
	});

	it('commits the sole match when nothing was arrowed to', () => {
		expect(resolveEnterSelection(-1, [three[2]])).toEqual(three[2]);
	});

	it('commits nothing when several match and none is active', () => {
		// There is no defensible pick here, and guessing one commits a payee the
		// user never looked at.
		expect(resolveEnterSelection(-1, three)).toBeNull();
	});

	it('commits nothing on an empty list', () => {
		expect(resolveEnterSelection(-1, [])).toBeNull();
		expect(resolveEnterSelection(0, [])).toBeNull();
	});

	it('commits nothing when the active index outran a shrunken list', () => {
		expect(resolveEnterSelection(7, three)).toBeNull();
	});
});

describe('revertedQuery', () => {
	it('restores the committed vendor label on blur', () => {
		expect(revertedQuery('Acme Corp (V-001)')).toBe('Acme Corp (V-001)');
	});

	it('empties the box when nothing is committed', () => {
		// This is what keeps a native `required` on the input honest: non-empty
		// text means exactly "a vendor is committed".
		expect(revertedQuery(null)).toBe('');
	});
});

describe('VENDOR_PICKER_PAGE_SIZE', () => {
	it('stays under the server MAX_PAGE_SIZE of 100', () => {
		expect(VENDOR_PICKER_PAGE_SIZE).toBeGreaterThan(0);
		expect(VENDOR_PICKER_PAGE_SIZE).toBeLessThanOrEqual(100);
	});
});
