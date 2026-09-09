import { describe, expect, it } from 'vitest';

import { KPI_NO_FIGURE, kpiDisplayValue, kpiFigureState } from './kpiValue';
import { formatMoney } from './money';

/**
 * The KPI "no figure" convention (`docs/decisions.md` §34 in a display layer).
 *
 * The two facts this file exists to hold apart, and which the `/discounts` KPI
 * row got wrong in both directions before it: a figure that has not been
 * computed must NOT render as a zero, and a figure that HAS been computed and
 * happens to be zero must still render as one. A guard that only asserted the
 * first would pass a component that hid every genuine zero.
 *
 * The rune-driven component itself is exercised in the e2e suite — vitest here
 * runs a plain-Node environment with no Svelte compiler, which is exactly why
 * the rule lives in a pure module rather than inline in `KpiCard.svelte`.
 */

describe('kpiFigureState', () => {
	it('reports a figure whenever one exists — including a computed zero', () => {
		// The whole point of the split: `0` is an answer. It is not the absence
		// of one, and nothing downstream may treat it as such.
		expect(kpiFigureState(0)).toBe('value');
		expect(kpiFigureState('0')).toBe('value');
		expect(kpiFigureState('$0.00')).toBe('value');
		expect(kpiFigureState('0%')).toBe('value');
		expect(kpiFigureState(1234)).toBe('value');
	});

	it('reports a missing figure as pending only while it is still arriving', () => {
		expect(kpiFigureState(null, true)).toBe('pending');
		expect(kpiFigureState(undefined, true)).toBe('pending');
	});

	it('reports a missing figure that is not in flight as unavailable', () => {
		// A dashboard request that FAILED must not leave the card claiming to
		// still be loading — nothing is coming, and the page's error banner is
		// what explains it.
		expect(kpiFigureState(null)).toBe('unavailable');
		expect(kpiFigureState(undefined, false)).toBe('unavailable');
	});

	it('never lets the pending flag blank a figure already on screen', () => {
		// `/discounts` re-fetches the dashboard after every accept/decline. A
		// caller that left the flag raised across a refresh would otherwise wipe
		// figures the reader is looking at.
		expect(kpiFigureState(0, true)).toBe('value');
		expect(kpiFigureState('$0.00', true)).toBe('value');
	});

	it('treats an empty string as a figure, not as an absence', () => {
		// Only `null`/`undefined` mean "nobody computed this". Widening the rule
		// to any falsy value would swallow `0` — the defect this module exists
		// to prevent — so the boundary is deliberately narrow and explicit.
		expect(kpiFigureState('')).toBe('value');
	});
});

describe('kpiDisplayValue', () => {
	it('renders a computed zero as a zero', () => {
		expect(kpiDisplayValue(0)).toBe('0');
		expect(kpiDisplayValue('$0.00')).toBe('$0.00');
		expect(kpiDisplayValue('0%')).toBe('0%');
	});

	it('renders the pre-response state as the no-figure dash, never as a zero', () => {
		// The regression under guard: `aggMoney(undefined, …)` used to format a
		// `0` for the whole load window, so Captured / Missed / Projected
		// savings each flashed a figure nobody had computed.
		const preResponse = kpiDisplayValue(null, true);
		expect(preResponse).toBe(KPI_NO_FIGURE);
		expect(preResponse).not.toBe('0');
		expect(preResponse).not.toMatch(/\d/);
	});

	it('renders an unavailable figure the same way it renders a pending one', () => {
		// Identical pixels by design — "still arriving" and "did not arrive" are
		// distinguished by what is ANNOUNCED (aria-busy + the screen-reader-only
		// loading text in KpiCard), not by a second glyph a sighted reader would
		// have to learn.
		expect(kpiDisplayValue(null, false)).toBe(kpiDisplayValue(null, true));
	});

	it("passes the caller's formatting through untouched", () => {
		// It must never re-format money: the caller has already applied the
		// response's own currency, which is not the org-default one.
		expect(kpiDisplayValue('€1,234')).toBe('€1,234');
	});
});

describe('KPI_NO_FIGURE', () => {
	it('is the same glyph formatMoney already falls back to', () => {
		// Drift guard. `formatMoney(null)` returns its `placeholder` default, and
		// a money KPI showing one dash while a `formatMoney(null)` two lines away
		// shows a different one would be two conventions pretending to be one.
		expect(formatMoney(null)).toBe(KPI_NO_FIGURE);
		expect(formatMoney(undefined, { currency: 'EUR', whole: true })).toBe(KPI_NO_FIGURE);
	});
});
