/**
 * Static token-pairing audit over the app's stylesheets.
 *
 * The route-by-route axe guard (`tests-e2e/a11y/axe.spec.ts`) checks whatever
 * a page happens to render at scan time. That leaves two blind spots this
 * module closes:
 *
 *  - A contrast failure recurs per **surface**, not per route. `--text-muted`
 *    on `--surface-2` was 4.34:1 wherever it appeared; axe caught it on the two
 *    `/admin` pages that were in the route list and missed the identical bug on
 *    `/billing`, which wasn't. A scan of the *stylesheets* finds every instance
 *    at once, including the ones behind a modal, an empty state, or a role the
 *    e2e user doesn't hold.
 *  - A `var(--token, fallback)` whose fallback disagrees with the token is a
 *    lie waiting to render. `--surface-2` shipped for months as
 *    `var(--surface-2, #232b44)` with the token undefined — the fallback was
 *    what rendered, and two call sites disagreed about its value.
 *
 * Everything here is pure: callers hand in already-read sources, so the module
 * is unit-testable on fixture strings and the repo-wide guard
 * (`tokenPairing.test.ts`) is just the file walk plus an assertion.
 *
 * **What it deliberately does not do:** resolve the cascade. A rule that sets
 * only `color` inherits its background from an ancestor, which is a runtime
 * question — axe owns that half. This audit checks pairs that are decided in a
 * single rule, which is where the palette's own mistakes live.
 */

import { contrastRatio, parseColor, WCAG_AA_LARGE, WCAG_AA_NORMAL } from './contrast';

export interface StyleSource {
	/** Repo-relative path, used only for reporting. */
	path: string;
	/** The CSS text — a whole `.css` file, or one `<style>` block's contents. */
	css: string;
}

export interface ContrastFinding {
	kind: 'contrast';
	path: string;
	selector: string;
	/** The declaration as written, e.g. `var(--text-muted)`. */
	foreground: string;
	background: string;
	/** …and what it resolved to, e.g. `#8a8fa0`. */
	foregroundColor: string;
	backgroundColor: string;
	ratio: number;
	required: number;
}

export interface StaleFallbackFinding {
	kind: 'stale-fallback';
	path: string;
	token: string;
	fallback: string;
	declared: string;
}

export interface DeadTokenFinding {
	kind: 'dead-token';
	path: string;
	token: string;
	fallback: string;
}

export interface LiteralTextColorFinding {
	kind: 'literal-text-color';
	path: string;
	selector: string;
	/** The literal as written. */
	color: string;
	colorValue: string;
	/** The surface token it failed against, and by how much. */
	surface: string;
	surfaceColor: string;
	ratio: number;
	required: number;
}

export type StyleFinding =
	| ContrastFinding
	| StaleFallbackFinding
	| DeadTokenFinding
	| LiteralTextColorFinding;

const TOKEN_RE = '--[a-z0-9-]+';

/** Strip `/* … *\/` comments so their contents never parse as CSS. */
export function stripCssComments(css: string): string {
	return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

/**
 * The CSS a file contributes: a `.css` file is all of it; a `.svelte` file is
 * the contents of its `<style>` blocks (a component can legally have more than
 * one, e.g. a `<style>` plus a `<style module>`).
 */
export function extractStyleBlocks(path: string, source: string): StyleSource[] {
	if (path.endsWith('.css')) return [{ path, css: source }];
	const blocks: StyleSource[] = [];
	const re = /<style[^>]*>([\s\S]*?)<\/style>/gi;
	let m: RegExpExecArray | null;
	while ((m = re.exec(source)) !== null) blocks.push({ path, css: m[1] });
	return blocks;
}

/** The `--token: value` pairs declared in a `:root` block. */
export function parsePalette(css: string): Record<string, string> {
	const palette: Record<string, string> = {};
	const re = /:root[^{]*\{([^{}]*)\}/g;
	let m: RegExpExecArray | null;
	while ((m = re.exec(stripCssComments(css))) !== null) {
		for (const [name, value] of parseDeclarations(m[1])) {
			if (name.startsWith('--')) palette[name] = value;
		}
	}
	return palette;
}

/**
 * Every custom property *assigned* anywhere in a file — CSS rules and inline
 * `style="--x: y"` attributes alike. A token assigned per-element (the
 * exceptions page's `--type-color`) is legitimately absent from `:root`, so it
 * must not read as dead.
 */
export function collectAssignedTokens(source: string): string[] {
	const found = new Set<string>();
	const re = new RegExp(`(${TOKEN_RE})\\s*:`, 'g');
	let m: RegExpExecArray | null;
	while ((m = re.exec(stripCssComments(source))) !== null) found.add(m[1]);
	return [...found];
}

/** `prop: value` pairs of a declaration block, in source order. */
export function parseDeclarations(block: string): [string, string][] {
	const out: [string, string][] = [];
	let depth = 0;
	let current = '';
	const flush = () => {
		const i = current.indexOf(':');
		if (i > 0) out.push([current.slice(0, i).trim().toLowerCase(), current.slice(i + 1).trim()]);
		current = '';
	};
	for (const ch of block) {
		if (ch === '(') depth++;
		else if (ch === ')') depth--;
		if (ch === ';' && depth === 0) flush();
		else current += ch;
	}
	flush();
	return out;
}

export interface CssRule {
	selector: string;
	declarations: [string, string][];
}

/**
 * Leaf rules (`selector { … }`). At-rule preludes (`@media (…) {`) are skipped
 * rather than parsed: the regex only matches blocks with no nested braces, so
 * a rule inside a media query is still found — it just loses the query in its
 * reported selector, which costs nothing here.
 */
export function parseRules(css: string): CssRule[] {
	const rules: CssRule[] = [];
	const re = /([^{}]+)\{([^{}]*)\}/g;
	let m: RegExpExecArray | null;
	while ((m = re.exec(stripCssComments(css))) !== null) {
		rules.push({
			selector: m[1].trim().replace(/\s+/g, ' '),
			declarations: parseDeclarations(m[2])
		});
	}
	return rules;
}

export interface VarReference {
	token: string;
	/** The fallback as written, or `null` when the `var()` has none. */
	fallback: string | null;
}

/**
 * Every `var()` reference in a stylesheet, with its fallback.
 *
 * Paren-aware rather than a regex, because a fallback can itself be a
 * `var()` — `var(--bg, var(--surface))` is in the codebase — and a
 * `[^()]*` capture silently matches nothing there. That would be a hole in
 * the exact guard this module exists to be: a rename staling one of those
 * nested fallbacks would go unreported by both the dead-token and the
 * stale-fallback check.
 */
export function findVarReferences(css: string): VarReference[] {
	const refs: VarReference[] = [];
	const tokenRe = new RegExp(`var\\(\\s*(${TOKEN_RE})`, 'gi');
	let match: RegExpExecArray | null;
	while ((match = tokenRe.exec(css)) !== null) {
		// Walk from the `var(` to its matching close paren, tracking depth.
		let depth = 1;
		let i = match.index + 'var('.length;
		let commaAt = -1;
		for (; i < css.length && depth > 0; i++) {
			const ch = css[i];
			if (ch === '(') depth++;
			else if (ch === ')') depth--;
			else if (ch === ',' && depth === 1 && commaAt < 0) commaAt = i;
		}
		if (depth !== 0) continue; // unbalanced — not something to judge
		const close = i - 1;
		refs.push({
			token: match[1],
			fallback: commaAt < 0 ? null : css.slice(commaAt + 1, close).trim()
		});
		// `exec` has already left `lastIndex` just past `var(--token`, which is
		// what we want: scanning resumes INSIDE the fallback, so a nested
		// var() is visited on its own terms rather than skipped with its
		// parent. `match[0]` is never empty, so this always advances.
	}
	return refs;
}

/** Split a value on top-level whitespace, keeping `rgba(1, 2, 3)` intact. */
function splitTopLevel(value: string): string[] {
	const parts: string[] = [];
	let depth = 0;
	let current = '';
	for (const ch of value) {
		if (ch === '(') depth++;
		else if (ch === ')') depth--;
		if (/\s/.test(ch) && depth === 0) {
			if (current) parts.push(current);
			current = '';
		} else current += ch;
	}
	if (current) parts.push(current);
	return parts;
}

/**
 * Resolve a declaration value to a literal colour, following `var()` chains
 * through the palette (and into a fallback when the token is undefined).
 * Returns `null` for anything whose rendered colour isn't statically known —
 * a gradient, a translucent value, `currentColor`, an unresolvable token.
 */
export function resolveColorValue(
	value: string,
	palette: Record<string, string>,
	depth = 0
): string | null {
	if (!value || depth > 8) return null;
	const v = value.trim().replace(/\s*!important$/i, '').trim();
	if (!v) return null;
	if (/gradient\(/i.test(v)) return null;

	const varMatch = new RegExp(`^var\\(\\s*(${TOKEN_RE})\\s*(?:,([\\s\\S]*))?\\)$`, 'i').exec(v);
	if (varMatch) {
		const declared = palette[varMatch[1]];
		if (declared !== undefined) return resolveColorValue(declared, palette, depth + 1);
		if (varMatch[2] !== undefined) return resolveColorValue(varMatch[2], palette, depth + 1);
		return null;
	}

	const direct = parseColor(v);
	if (direct) return toHex(direct);

	// A shorthand like `background: var(--surface) url(x) no-repeat` — the
	// colour is one of the top-level parts.
	const parts = splitTopLevel(v);
	if (parts.length > 1) {
		for (const part of parts) {
			const resolved = resolveColorValue(part, palette, depth + 1);
			if (resolved) return resolved;
		}
	}
	return null;
}

function toHex({ r, g, b }: { r: number; g: number; b: number }): string {
	return `#${[r, g, b].map((n) => n.toString(16).padStart(2, '0')).join('')}`;
}

/**
 * Is the text in this rule "large" per SC 1.4.3 — 24px at any weight, or
 * 18.66px bold? Only a size declared *in the same rule* counts; an inherited
 * or `em`-relative size is unknowable here and is treated as normal text,
 * which is the stricter direction.
 */
export function isLargeText(declarations: [string, string][]): boolean {
	const decls = Object.fromEntries(declarations);
	const size = decls['font-size'];
	if (!size) return false;
	let px: number | null = null;
	const rem = /^([\d.]+)rem$/.exec(size.trim());
	const abs = /^([\d.]+)px$/.exec(size.trim());
	if (rem) px = parseFloat(rem[1]) * 16;
	else if (abs) px = parseFloat(abs[1]);
	if (px === null) return false;
	if (px >= 24) return true;
	const weight = (decls['font-weight'] ?? '').trim().toLowerCase();
	const bold = weight === 'bold' || weight === 'bolder' || parseInt(weight, 10) >= 700;
	return bold && px >= 18.66;
}

export interface AuditOptions {
	palette: Record<string, string>;
	/** Every custom property assigned anywhere in the scanned tree. */
	assignedTokens: Set<string>;
	/**
	 * Palette tokens a bare literal `color:` is held against — the surfaces
	 * body text actually sits on. `--surface-2` is deliberately NOT here: it's
	 * a raised panel used in a handful of places, and a rule that puts text on
	 * it declares the background, so the same-rule pair check already covers
	 * it. Including it would flag every red/green status literal in the app on
	 * the strength of a surface it never renders against.
	 */
	textSurfaces?: string[];
}

/**
 * Colours exempt from the bare-literal rule: they are the deliberate
 * on-a-coloured-fill choices, whose background legitimately comes from a
 * parent or sibling rule the scanner can't see.
 */
const LITERAL_EXEMPT = new Set(['#ffffff', '#000000']);

/**
 * Run all three checks over the given stylesheets. Findings are returned, not
 * thrown, so the caller decides how to report them.
 */
export function auditStyles(sources: StyleSource[], options: AuditOptions): StyleFinding[] {
	const findings: StyleFinding[] = [];
	const { palette, assignedTokens, textSurfaces = [] } = options;

	for (const source of sources) {
		const css = stripCssComments(source.css);

		// 1 + 2 — every var() reference: is the token real, and does a fallback
		// contradict it?
		for (const { token, fallback } of findVarReferences(css)) {
			if (fallback === null) continue; // nothing to contradict, nothing dead to hide
			const declared = palette[token];
			if (declared === undefined) {
				if (!assignedTokens.has(token)) {
					findings.push({ kind: 'dead-token', path: source.path, token, fallback });
				}
			} else if (!sameValue(declared, fallback, palette)) {
				findings.push({
					kind: 'stale-fallback',
					path: source.path,
					token,
					fallback,
					declared
				});
			}
		}

		// 3 — contrast of pairs decided within one rule.
		for (const rule of parseRules(source.css)) {
			let foreground: string | null = null;
			let background: string | null = null;
			for (const [prop, value] of rule.declarations) {
				if (prop === 'color') foreground = value;
				// Later declaration wins, and the `background` shorthand resets
				// `background-color`, so plain assignment models both.
				else if (prop === 'background' || prop === 'background-color') background = value;
			}
			const resolvedBackground = background ? resolveColorValue(background, palette) : null;

			// 4 — a literal `color:` whose rule states no OPAQUE background.
			// Whatever is behind it comes from the cascade — nothing at all, a
			// translucent tint, a gradient — so the only sound question is
			// whether the literal is legible on the surfaces body text sits on.
			// (A translucent tint over one of those surfaces composites close
			// to it, so holding the literal to the bare surface is the right
			// approximation, and the conservative one.) A palette token is
			// exempt because `palette contract` asserts each one against those
			// same surfaces directly.
			if (foreground && !resolvedBackground) {
				const fg = resolveColorValue(foreground, palette);
				const isToken = /^var\(/i.test(foreground.trim());
				if (fg && !isToken && !LITERAL_EXEMPT.has(fg)) {
					const required = isLargeText(rule.declarations) ? WCAG_AA_LARGE : WCAG_AA_NORMAL;
					for (const token of textSurfaces) {
						const surfaceColor = palette[token];
						if (!surfaceColor) continue;
						const ratio = contrastRatio(fg, surfaceColor);
						if (ratio === null || ratio + 1e-9 >= required) continue;
						findings.push({
							kind: 'literal-text-color',
							path: source.path,
							selector: rule.selector,
							color: foreground,
							colorValue: fg,
							surface: token,
							surfaceColor,
							ratio,
							required
						});
						break; // one finding per rule — the first failing surface names it
					}
				}
			}

			if (!foreground || !background || !resolvedBackground) continue;

			const fg = resolveColorValue(foreground, palette);
			const bg = resolvedBackground;
			if (!fg) continue;

			const ratio = contrastRatio(fg, bg);
			if (ratio === null) continue;
			const required = isLargeText(rule.declarations) ? WCAG_AA_LARGE : WCAG_AA_NORMAL;
			if (ratio + 1e-9 < required) {
				findings.push({
					kind: 'contrast',
					path: source.path,
					selector: rule.selector,
					foreground,
					background,
					foregroundColor: fg,
					backgroundColor: bg,
					ratio,
					required
				});
			}
		}
	}
	return findings;
}

/**
 * Compare two CSS values for "the same colour", so `#FFF` === `white` and a
 * fallback written as another token (`var(--bg, var(--surface))`) is compared
 * by the colour it resolves to, not by its spelling. Non-colour values (a font
 * stack) fall back to a normalized string compare.
 */
function sameValue(a: string, b: string, palette: Record<string, string>): boolean {
	const normalize = (v: string) => {
		const resolved = resolveColorValue(v, palette);
		if (resolved) return resolved;
		const parsed = parseColor(v);
		return parsed ? toHex(parsed) : v.trim().toLowerCase().replace(/\s+/g, ' ');
	};
	return normalize(a) === normalize(b);
}

/** One-line, copy-pasteable description used in the guard's failure output. */
export function describeFinding(finding: StyleFinding): string {
	switch (finding.kind) {
		case 'contrast':
			return (
				`${finding.path} — {${finding.selector}}: ` +
				`color ${finding.foreground} (${finding.foregroundColor}) on ` +
				`${finding.background} (${finding.backgroundColor}) is ` +
				`${finding.ratio.toFixed(2)}:1, below the ${finding.required}:1 bar`
			);
		case 'stale-fallback':
			return (
				`${finding.path} — var(${finding.token}, ${finding.fallback}) contradicts the ` +
				`declared ${finding.token}: ${finding.declared}; drop the fallback`
			);
		case 'dead-token':
			return (
				`${finding.path} — var(${finding.token}, ${finding.fallback}) references a token ` +
				`nothing ever assigns, so the fallback is what always renders; ` +
				`define ${finding.token} or inline the value`
			);
		case 'literal-text-color':
			return (
				`${finding.path} — {${finding.selector}}: color ${finding.color} ` +
				`(${finding.colorValue}) is ${finding.ratio.toFixed(2)}:1 on ${finding.surface} ` +
				`(${finding.surfaceColor}), below the ${finding.required}:1 bar. The rule sets no ` +
				`background, so this text renders on an app surface — use a palette token`
			);
	}
}
