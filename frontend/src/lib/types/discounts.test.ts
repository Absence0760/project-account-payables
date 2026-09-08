import { describe, it, expect } from 'vitest';
import type { DiscountOptimization, DiscountRoi } from './discounts';
import { en } from '$lib/i18n/locales/en';
import { interpolate } from '$lib/i18n/interpolate';

/**
 * Guard on the optimizer's two-list contract.
 *
 * `POST /api/discounts/optimize` returns `recommendations` (ranked by APR) AND
 * `unrankable` — offers with no net due date, whose ROI horizon is unknown.
 * The server used to substitute the discount deadline for the missing due
 * date, which reported a fabricated `0.00 %` APR beside a positive net
 * benefit; it now withholds the horizon-relative fields instead.
 *
 * Rendering the second list is the whole point of the frontend half: an offer
 * the page does not render is ABSENT, and a real capturable saving that
 * disappears from the UI is a worse failure than the wrong number it replaced.
 */
describe('the unrankable copy', () => {
	it('exists in the catalogue', () => {
		for (const key of [
			'discounts.opt.unrankableHeading',
			'discounts.opt.unrankableNote',
			'discounts.opt.aprUnknown',
			'discounts.opt.notRanked'
		] as const) {
			expect(Object.keys(en), `"${key}" is not in the catalogue`).toContain(key);
			expect(en[key].trim().length).toBeGreaterThan(0);
		}
	});

	it('counts the offers it is describing', () => {
		// The note carries `{n}` so it can say how many offers are outside the
		// ranking — "some offers" would leave the reader unable to tell whether
		// the section is an edge case or most of their savings.
		expect(en['discounts.opt.unrankableNote']).toContain('{n');
		expect(interpolate(en['discounts.opt.unrankableNote'], { n: 1 }, 'en')).toContain('1 offer ');
		expect(interpolate(en['discounts.opt.unrankableNote'], { n: 4 }, 'en')).toContain('4 offers ');
	});

	it('never states a rate for an unknown horizon', () => {
		// The failure mode this replaced: a 0 % APR presented as measured. The
		// strings must say "unknown", not a number.
		expect(en['discounts.opt.aprUnknown']).not.toMatch(/\d/);
		expect(en['discounts.opt.unrankableHeading']).not.toMatch(/\d/);
	});
});

describe('DiscountRoi', () => {
	it('reads an unrankable ROI as unknown, not as zero', () => {
		// The shape the server sends for a horizonless offer: the saving is real
		// (a percentage of the base amount — horizon-free), everything the
		// horizon feeds is null, and `worthwhile` is null meaning CANNOT RANK,
		// which is a different answer from `false` (ranked, and it loses).
		const roi: DiscountRoi = {
			base_amount: '10000.00',
			discount_percent: 2,
			days_accelerated: null,
			savings: '200.00',
			annualized_return_pct: null,
			cost_of_capital_pct: 8,
			opportunity_cost: null,
			net_benefit: null,
			worthwhile: null,
			horizon_known: false
		};
		expect(roi.annualized_return_pct).toBeNull();
		expect(roi.savings).toBe('200.00');
		// The page tints the row on `worthwhile === true`, never on truthiness:
		// `null` must not read as a decided negative verdict either.
		expect(roi.worthwhile === true).toBe(false);
		expect(roi.worthwhile === false).toBe(false);
	});

	it('treats a payload with no unrankable array as having none', () => {
		// The field is optional so a response predating it still parses; the
		// page reads `?? []` rather than throwing on `undefined.length`.
		const older: DiscountOptimization = {
			cash_budget: null,
			currency: 'USD',
			cost_of_capital_pct: 8,
			total_savings_available: '0.00',
			total_savings_selected: '0.00',
			total_outlay_selected: '0.00',
			unconvertible_count: 0,
			recommendations: []
		};
		expect(older.unrankable ?? []).toEqual([]);
	});
});

/**
 * Type-level half, in the shape `moneyTypeAudit.test.ts` uses: TypeScript
 * fails an UNUSED `@ts-expect-error`, so re-widening these fields back to
 * non-nullable turns `pnpm check` red as well as this file. Never resolve one
 * with an `as` cast — that puts the fabricated 0.00 % straight back.
 */
// eslint-disable-next-line @typescript-eslint/no-unused-vars
function unknownHorizonMustNotCompile(roi: DiscountRoi) {
	// @ts-expect-error a withheld APR is null — render "unknown", never 0.0.
	const apr = roi.annualized_return_pct.toFixed(1);
	// @ts-expect-error an unknown horizon has no day count to do maths on.
	const weeks = roi.days_accelerated / 7;
	return { apr, weeks };
}
