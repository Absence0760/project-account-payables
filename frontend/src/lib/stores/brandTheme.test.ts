import { describe, it, expect } from 'vitest';
import {
	accentStrongContrast,
	accentStrongMeetsAA,
	isValidHexColor,
	brandThemeVars,
	type Brand
} from './brandTheme';

function makeBrand(overrides: Partial<Brand> = {}): Brand {
	return {
		product_name: '',
		logo_url: '',
		accent_color: '',
		accent_strong_color: '',
		support_url: '',
		legal_url: '',
		...overrides
	};
}

describe('isValidHexColor', () => {
	it('accepts 6- and 3-digit hex', () => {
		expect(isValidHexColor('#638cff')).toBe(true);
		expect(isValidHexColor('#abc')).toBe(true);
		expect(isValidHexColor('  #ABCDEF  ')).toBe(true); // trimmed
	});

	it('rejects non-hex / wrong-length / missing-hash', () => {
		expect(isValidHexColor('638cff')).toBe(false);
		expect(isValidHexColor('#zzzzzz')).toBe(false);
		expect(isValidHexColor('#1234')).toBe(false);
		expect(isValidHexColor('red')).toBe(false);
		expect(isValidHexColor('rgb(1,2,3)')).toBe(false);
		expect(isValidHexColor('')).toBe(false);
		expect(isValidHexColor(null)).toBe(false);
		expect(isValidHexColor(undefined)).toBe(false);
	});
});

describe('brandThemeVars (fallback logic)', () => {
	it('returns no vars when nothing is configured (defaults stand)', () => {
		expect(brandThemeVars(makeBrand())).toEqual({});
	});

	it('emits --accent only when accent_color is a valid hex', () => {
		expect(brandThemeVars(makeBrand({ accent_color: '#112233' }))).toEqual({
			'--accent': '#112233'
		});
	});

	it('emits --accent-strong only when accent_strong_color is a valid hex', () => {
		expect(brandThemeVars(makeBrand({ accent_strong_color: '#0a1622' }))).toEqual({
			'--accent-strong': '#0a1622'
		});
	});

	it('emits both when both are valid', () => {
		expect(
			brandThemeVars(makeBrand({ accent_color: '#abc', accent_strong_color: '#def' }))
		).toEqual({ '--accent': '#abc', '--accent-strong': '#def' });
	});

	it('omits an invalid color so the app.css default is kept', () => {
		// A malformed accent must NOT be written — the default token wins.
		expect(brandThemeVars(makeBrand({ accent_color: 'not-a-color' }))).toEqual({});
		expect(
			brandThemeVars(makeBrand({ accent_color: '#112233', accent_strong_color: 'bogus' }))
		).toEqual({ '--accent': '#112233' });
	});

	it('trims whitespace around a valid color', () => {
		expect(brandThemeVars(makeBrand({ accent_color: '  #112233  ' }))).toEqual({
			'--accent': '#112233'
		});
	});
});

/**
 * `--accent-strong` exists so white text has somewhere legible to sit, and
 * `brandThemeVars` hands the tenant's raw hex straight to it. The static
 * token-pairing guard can't see that override, so this is the runtime half of
 * the same check.
 */
describe('accentStrongContrast / accentStrongMeetsAA', () => {
	it('reports the white-on-colour ratio for the shipped default', () => {
		// app.css --accent-strong: #3f5fd6
		expect(accentStrongContrast('#3f5fd6') as number).toBeCloseTo(5.5, 1);
		expect(accentStrongMeetsAA('#3f5fd6')).toBe(true);
	});

	it('fails a brand colour too light to carry white text', () => {
		// A logo yellow is the realistic bad case, not a contrived one.
		expect(accentStrongMeetsAA('#ffe066')).toBe(false);
		// And the plain --accent value, which is exactly why the strong
		// companion exists.
		expect(accentStrongContrast('#638cff') as number).toBeCloseTo(3.12, 2);
		expect(accentStrongMeetsAA('#638cff')).toBe(false);
	});

	it('accepts 3-digit hex and surrounding whitespace, like the validator', () => {
		expect(accentStrongMeetsAA('  #000  ')).toBe(true);
	});

	/**
	 * Null, not false — a half-typed or empty field must not flash a warning,
	 * and "not a colour" is a different state from "fails".
	 */
	it('returns null when there is nothing to judge', () => {
		for (const value of ['', '   ', '#12', 'rebeccapurple', null, undefined]) {
			expect(accentStrongContrast(value), String(value)).toBeNull();
			expect(accentStrongMeetsAA(value), String(value)).toBeNull();
		}
	});
});
