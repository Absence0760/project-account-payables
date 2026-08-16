import { describe, expect, it } from 'vitest';
import {
	contrastRatio,
	formatRatio,
	meetsContrastAA,
	parseColor,
	relativeLuminance,
	WCAG_AA_LARGE,
	WCAG_AA_NORMAL
} from './contrast';

describe('parseColor', () => {
	it('parses 6-digit hex', () => {
		expect(parseColor('#638cff')).toEqual({ r: 0x63, g: 0x8c, b: 0xff });
	});

	it('parses 3-digit hex by doubling each nibble', () => {
		expect(parseColor('#fff')).toEqual({ r: 255, g: 255, b: 255 });
		expect(parseColor('#1a2')).toEqual({ r: 0x11, g: 0xaa, b: 0x22 });
	});

	it('is case-insensitive and tolerates surrounding whitespace / !important', () => {
		expect(parseColor('  #E2E4EA !important ')).toEqual({ r: 0xe2, g: 0xe4, b: 0xea });
	});

	it('accepts the two keywords the palette uses', () => {
		expect(parseColor('white')).toEqual({ r: 255, g: 255, b: 255 });
		expect(parseColor('black')).toEqual({ r: 0, g: 0, b: 0 });
	});

	it('parses opaque rgb()/rgba() in comma and space syntax', () => {
		expect(parseColor('rgb(99, 140, 255)')).toEqual({ r: 99, g: 140, b: 255 });
		expect(parseColor('rgb(99 140 255)')).toEqual({ r: 99, g: 140, b: 255 });
		expect(parseColor('rgba(99, 140, 255, 1)')).toEqual({ r: 99, g: 140, b: 255 });
		expect(parseColor('rgb(99 140 255 / 100%)')).toEqual({ r: 99, g: 140, b: 255 });
	});

	it('scales percentage colour channels to bytes', () => {
		expect(parseColor('rgb(100%, 0%, 50%)')).toEqual({ r: 255, g: 0, b: 128 });
	});

	/**
	 * The whole point of returning null rather than a colour: a translucent
	 * value composites against whatever is behind it, which no static check
	 * can know. A caller must treat it as "unknown", never as a pass.
	 */
	it('refuses translucent colours rather than guessing the composite', () => {
		expect(parseColor('rgba(99, 140, 255, 0.35)')).toBeNull();
		expect(parseColor('rgb(99 140 255 / 50%)')).toBeNull();
		expect(parseColor('#638cff80')).toBeNull();
	});

	it('accepts #rrggbbff, which is opaque', () => {
		expect(parseColor('#638cffff')).toEqual({ r: 0x63, g: 0x8c, b: 0xff });
	});

	it('refuses context-dependent and non-colour values', () => {
		for (const v of [
			'',
			'   ',
			'transparent',
			'currentcolor',
			'inherit',
			'var(--accent)',
			'linear-gradient(#fff, #000)',
			'#12345',
			'rgb(1, 2)',
			'rgb(a, b, c)'
		]) {
			expect(parseColor(v), v).toBeNull();
		}
	});
});

describe('relativeLuminance', () => {
	it('anchors at the sRGB extremes', () => {
		expect(relativeLuminance({ r: 255, g: 255, b: 255 })).toBeCloseTo(1, 10);
		expect(relativeLuminance({ r: 0, g: 0, b: 0 })).toBeCloseTo(0, 10);
	});

	it('applies the linear segment below the 0.03928 knee', () => {
		// 10/255 = 0.0392 — just under the knee, so the /12.92 branch.
		expect(relativeLuminance({ r: 10, g: 10, b: 10 })).toBeCloseTo(10 / 255 / 12.92, 10);
	});
});

describe('contrastRatio', () => {
	it('spans the full 1–21 range', () => {
		expect(contrastRatio('#000', '#fff')).toBeCloseTo(21, 6);
		expect(contrastRatio('#777', '#777')).toBeCloseTo(1, 6);
	});

	it('is order-independent', () => {
		expect(contrastRatio('#8a8fa0', '#232b44')).toBeCloseTo(
			contrastRatio('#232b44', '#8a8fa0') as number,
			10
		);
	});

	/**
	 * The regression this whole guard exists for: --text-muted on --surface-2
	 * is 4.34:1, under the 4.5:1 bar. Pinning the number means a palette tweak
	 * that "fixes" it by accident still has to state the new value.
	 */
	it('reproduces the --text-muted on --surface-2 failure at 4.34:1', () => {
		expect(contrastRatio('#8a8fa0', '#232b44')).toBeCloseTo(4.34, 2);
	});

	it('reproduces white-on---accent at 3.12:1 and white-on---accent-strong above the bar', () => {
		expect(contrastRatio('#fff', '#638cff')).toBeCloseTo(3.12, 2);
		expect(contrastRatio('#fff', '#3f5fd6') as number).toBeGreaterThanOrEqual(WCAG_AA_NORMAL);
	});

	it('returns null when either side is unresolvable', () => {
		expect(contrastRatio('#fff', 'transparent')).toBeNull();
		expect(contrastRatio('var(--accent)', '#fff')).toBeNull();
	});

	it('accepts already-parsed Rgb on either side', () => {
		expect(contrastRatio({ r: 0, g: 0, b: 0 }, '#fff')).toBeCloseTo(21, 6);
	});
});

describe('meetsContrastAA', () => {
	it('holds normal text to 4.5:1 and large text to 3:1', () => {
		// 3.12:1 — fails as normal text, passes as large text.
		expect(meetsContrastAA('#fff', '#638cff')).toBe(false);
		expect(meetsContrastAA('#fff', '#638cff', true)).toBe(true);
	});

	it('passes a comfortable pair at both sizes', () => {
		expect(meetsContrastAA('#e2e4ea', '#0f1117')).toBe(true);
		expect(meetsContrastAA('#e2e4ea', '#0f1117', true)).toBe(true);
	});

	it('returns null — not false, and not true — for an unresolvable pair', () => {
		expect(meetsContrastAA('#fff', 'transparent')).toBeNull();
	});

	it('uses the exported thresholds', () => {
		expect(WCAG_AA_NORMAL).toBe(4.5);
		expect(WCAG_AA_LARGE).toBe(3);
	});
});

describe('formatRatio', () => {
	it('renders two decimals with the conventional :1 suffix', () => {
		expect(formatRatio(4.3421)).toBe('4.34:1');
		expect(formatRatio(21)).toBe('21.00:1');
	});
});
