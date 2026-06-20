import { describe, it, expect } from 'vitest';
import { isValidHexColor, brandThemeVars, type Brand } from './brandTheme';

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
