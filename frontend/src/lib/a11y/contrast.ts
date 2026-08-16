/**
 * WCAG 2.2 colour-contrast primitives (SC 1.4.3 Contrast (Minimum)).
 *
 * Pure, dependency-free, and framework-free so it runs identically in three
 * places:
 *
 *  1. `cssAudit.ts` → the build-time token-pairing guard that scans every
 *     stylesheet in `src/` (see `tokenPairing.test.ts`);
 *  2. the `/organization` white-label branding form, which lets a tenant
 *     overwrite `--accent-strong` — the token whose entire contract is
 *     "white text sits on this" — with any hex it likes;
 *  3. any future check that needs to answer "is this foreground legible on
 *     that background".
 *
 * Only opaque colours are resolvable. A translucent value composites against
 * whatever is behind it, which is a cascade question no static check can
 * answer, so those return `null` and callers treat the pair as unknown rather
 * than guessing a verdict.
 */

export interface Rgb {
	r: number;
	g: number;
	b: number;
}

/** SC 1.4.3 threshold for normal-size text. */
export const WCAG_AA_NORMAL = 4.5;

/**
 * SC 1.4.3 threshold for *large* text — 18.66px (14pt) bold, or 24px (18pt) at
 * any weight. Only applies when the size is actually known; an unresolvable
 * size is treated as normal text, which is the fail-closed direction.
 */
export const WCAG_AA_LARGE = 3;

/** The two CSS colour keywords the palette actually uses. */
const KEYWORDS: Record<string, Rgb> = {
	white: { r: 255, g: 255, b: 255 },
	black: { r: 0, g: 0, b: 0 }
};

const clampByte = (n: number) => Math.max(0, Math.min(255, Math.round(n)));

/** A parsed colour plus its alpha. `alpha` is 1 for every opaque notation. */
export interface RgbaParts {
	color: Rgb;
	alpha: number;
}

/**
 * Parse a CSS colour literal, **keeping** its alpha. Returns `null` only for
 * something whose colour isn't a literal at all — a gradient, `transparent` /
 * `currentColor`, an unresolved `var()`. Resolve `var()` before calling this
 * (see `cssAudit.resolveColorValue`).
 *
 * Exists because a translucent value is not unknowable — it is knowable *given
 * what is behind it*. `parseColor` (below) deliberately drops those, so a
 * caller that can supply the backdrop uses this and `compositeOver` instead.
 */
export function parseColorWithAlpha(value: string): RgbaParts | null {
	if (!value) return null;
	const v = value.trim().toLowerCase().replace(/\s*!important$/, '').trim();
	if (!v) return null;

	if (KEYWORDS[v]) return { color: KEYWORDS[v], alpha: 1 };

	if (/^#[0-9a-f]{3}$/.test(v)) {
		return {
			color: {
				r: parseInt(v[1] + v[1], 16),
				g: parseInt(v[2] + v[2], 16),
				b: parseInt(v[3] + v[3], 16)
			},
			alpha: 1
		};
	}
	if (/^#[0-9a-f]{6}$/.test(v)) {
		return {
			color: {
				r: parseInt(v.slice(1, 3), 16),
				g: parseInt(v.slice(3, 5), 16),
				b: parseInt(v.slice(5, 7), 16)
			},
			alpha: 1
		};
	}
	if (/^#[0-9a-f]{8}$/.test(v)) {
		return {
			color: {
				r: parseInt(v.slice(1, 3), 16),
				g: parseInt(v.slice(3, 5), 16),
				b: parseInt(v.slice(5, 7), 16)
			},
			alpha: parseInt(v.slice(7, 9), 16) / 255
		};
	}

	const fn = /^rgba?\(([^)]*)\)$/.exec(v);
	if (fn) {
		const slots = fn[1].split(/[\s,/]+/).filter(Boolean);
		if (slots.length !== 3 && slots.length !== 4) return null;
		const nums = slots.map((s) => parseFloat(s));
		if (nums.some((n) => Number.isNaN(n))) return null;
		// Alpha is a 0–1 number or a percentage — never a 0–255 byte.
		const alpha =
			slots.length === 4 ? (slots[3].endsWith('%') ? nums[3] / 100 : nums[3]) : 1;
		if (alpha < 0 || alpha > 1) return null;
		// Colour channels may be bytes (`255`) or percentages (`100%`).
		const channel = (i: number) => (slots[i].endsWith('%') ? (nums[i] / 100) * 255 : nums[i]);
		return {
			color: { r: clampByte(channel(0)), g: clampByte(channel(1)), b: clampByte(channel(2)) },
			alpha
		};
	}

	return null;
}

/**
 * Composite a translucent colour over an opaque backdrop (simple source-over).
 * This is what the browser does, and what axe measures — so a rule that tints
 * its background, or sits under an ancestor `opacity`, has a knowable rendered
 * colour as soon as the backdrop is named.
 */
export function compositeOver(source: Rgb, backdrop: Rgb, alpha: number): Rgb {
	const blend = (s: number, b: number) => clampByte(alpha * s + (1 - alpha) * b);
	return {
		r: blend(source.r, backdrop.r),
		g: blend(source.g, backdrop.g),
		b: blend(source.b, backdrop.b)
	};
}

/**
 * Parse a CSS colour literal into RGB. Returns `null` for anything whose
 * rendered colour depends on context — a translucent value, a gradient, a
 * keyword like `transparent` / `currentColor`, or an unresolved `var()`.
 * Resolve `var()` before calling this (see `cssAudit.resolveColorValue`).
 */
export function parseColor(value: string): Rgb | null {
	const parsed = parseColorWithAlpha(value);
	if (!parsed || parsed.alpha < 1) return null;
	return parsed.color;
}

/** WCAG relative luminance of an opaque sRGB colour. */
export function relativeLuminance({ r, g, b }: Rgb): number {
	const channel = (byte: number) => {
		const c = byte / 255;
		return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
	};
	return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/**
 * WCAG contrast ratio between two colours, 1–21. Order-independent.
 * Returns `null` when either side can't be resolved to an opaque colour.
 */
export function contrastRatio(a: string | Rgb, b: string | Rgb): number | null {
	const rgbA = typeof a === 'string' ? parseColor(a) : a;
	const rgbB = typeof b === 'string' ? parseColor(b) : b;
	if (!rgbA || !rgbB) return null;
	const lA = relativeLuminance(rgbA);
	const lB = relativeLuminance(rgbB);
	const lighter = Math.max(lA, lB);
	const darker = Math.min(lA, lB);
	return (lighter + 0.05) / (darker + 0.05);
}

/**
 * Does this foreground/background pair clear SC 1.4.3? An unresolvable pair
 * returns `null` — "unknown", never a silent pass, so a caller has to decide
 * what to do about it rather than inheriting a false green.
 */
export function meetsContrastAA(
	foreground: string | Rgb,
	background: string | Rgb,
	largeText = false
): boolean | null {
	const ratio = contrastRatio(foreground, background);
	if (ratio === null) return null;
	return ratio >= (largeText ? WCAG_AA_LARGE : WCAG_AA_NORMAL);
}

/** Round a ratio the way it is conventionally reported (`4.34:1`). */
export function formatRatio(ratio: number): string {
	return `${ratio.toFixed(2)}:1`;
}
