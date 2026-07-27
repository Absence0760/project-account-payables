/**
 * Pure white-label theming helpers, kept in a plain `.ts` module (no Svelte
 * runes) so they're unit-testable under the runtime-free vitest config — the
 * rune store in `brand.svelte.ts` re-exports them. Mirrors the backend
 * `BrandConfig` validators.
 */

export interface Brand {
	product_name: string;
	logo_url: string;
	accent_color: string;
	accent_strong_color: string;
	support_url: string;
	legal_url: string;
}

export const EMPTY_BRAND: Brand = {
	product_name: '',
	logo_url: '',
	accent_color: '',
	accent_strong_color: '',
	support_url: '',
	legal_url: ''
};

export const DEFAULT_PRODUCT_NAME = 'FeohLedger';

/**
 * Strict guard mirroring the backend — a 3- or 6-digit hex literal. The value
 * is written into a CSS custom property, so anything that isn't a plain color
 * is rejected to keep the cascade clean.
 */
export function isValidHexColor(value: string | null | undefined): boolean {
	return typeof value === 'string' && /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(value.trim());
}

/**
 * The CSS custom-property overrides to apply for a brand — only the keys the
 * org configured with a *valid* color. An unset or malformed color is omitted
 * so the `src/app.css` default token stands.
 */
export function brandThemeVars(brand: Brand): Record<string, string> {
	const vars: Record<string, string> = {};
	if (isValidHexColor(brand.accent_color)) vars['--accent'] = brand.accent_color.trim();
	if (isValidHexColor(brand.accent_strong_color))
		vars['--accent-strong'] = brand.accent_strong_color.trim();
	return vars;
}
