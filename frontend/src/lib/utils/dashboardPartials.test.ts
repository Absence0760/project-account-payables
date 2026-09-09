import { describe, expect, it } from 'vitest';

import {
	partialLabels,
	totalUnconverted,
	type PartialSeriesEntry
} from './dashboardPartials';

interface Vendor extends PartialSeriesEntry {
	vendor: string;
}

interface Month extends PartialSeriesEntry {
	month: string;
}

function vendors(...rows: [string, number][]): Vendor[] {
	return rows.map(([vendor, unconverted_count]) => ({ vendor, unconverted_count }));
}

function months(...rows: [string, number][]): Month[] {
	return rows.map(([month, unconverted_count]) => ({ month, unconverted_count }));
}

describe('totalUnconverted', () => {
	it('is zero when every row converted', () => {
		expect(totalUnconverted(vendors(['Globex', 0], ['Initech', 0]))).toBe(0);
	});

	it('sums across the series', () => {
		expect(totalUnconverted(vendors(['Globex', 2], ['Initech', 0], ['Umbrella', 3]))).toBe(5);
	});

	it('is zero for an empty or absent series', () => {
		// An empty tenant renders no chart at all, and a failed fetch leaves the
		// whole payload null — neither is a disclosure.
		expect(totalUnconverted([])).toBe(0);
		expect(totalUnconverted(null)).toBe(0);
		expect(totalUnconverted(undefined)).toBe(0);
	});

	it('ignores a malformed count rather than propagating it', () => {
		// These counts come off the wire. A negative one must not CANCEL a real
		// exclusion out — that would turn a genuine disclosure into silence,
		// which is the exact failure the disclosure exists to prevent.
		const rows = [
			{ unconverted_count: 4 },
			{ unconverted_count: -3 },
			{ unconverted_count: Number.NaN },
			{ unconverted_count: undefined as unknown as number }
		];
		expect(totalUnconverted(rows)).toBe(4);
	});

	it('floors a fractional count instead of reporting "1.5 invoices"', () => {
		expect(totalUnconverted([{ unconverted_count: 1.5 }])).toBe(1);
	});
});

describe('partialLabels', () => {
	it('names only the entries that folded a row at face value', () => {
		const rows = vendors(['Globex', 0], ['Initech', 2], ['Umbrella', 0], ['Soylent', 1]);
		expect(partialLabels(rows, (v) => v.vendor)).toEqual(['Initech', 'Soylent']);
	});

	it('keeps the series order, so the names read against the chart', () => {
		// The vendor tile is rank-ordered and the trend chart is chronological;
		// re-sorting the names would make the notice harder to match to the bars
		// than the bars are to each other.
		const rows = months(['2026-04', 1], ['2026-05', 0], ['2026-06', 2]);
		expect(partialLabels(rows, (t) => t.month)).toEqual(['2026-04', '2026-06']);
	});

	it('is empty when everything converted', () => {
		expect(partialLabels(vendors(['Globex', 0]), (v) => v.vendor)).toEqual([]);
	});

	it('is empty for an absent series', () => {
		expect(partialLabels(null, (v: Vendor) => v.vendor)).toEqual([]);
		expect(partialLabels(undefined, (v: Vendor) => v.vendor)).toEqual([]);
	});

	it('applies the same malformed-count rule as the total', () => {
		// Otherwise the banner could name a vendor while reporting zero
		// invoices, or count one it does not name.
		const rows = [
			{ vendor: 'Globex', unconverted_count: -1 },
			{ vendor: 'Initech', unconverted_count: Number.NaN },
			{ vendor: 'Umbrella', unconverted_count: 1 }
		];
		expect(partialLabels(rows, (v) => v.vendor)).toEqual(['Umbrella']);
		expect(totalUnconverted(rows)).toBe(1);
	});
});
