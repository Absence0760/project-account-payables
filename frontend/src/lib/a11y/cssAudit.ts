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

export type StyleFinding = ContrastFinding | StaleFallbackFinding | DeadTokenFinding;

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
}

/**
 * Run all three checks over the given stylesheets. Findings are returned, not
 * thrown, so the caller decides how to report them.
 */
export function auditStyles(sources: StyleSource[], options: AuditOptions): StyleFinding[] {
	const findings: StyleFinding[] = [];
	const { palette, assignedTokens } = options;

	for (const source of sources) {
		const css = stripCssComments(source.css);

		// 1 + 2 — every var() reference: is the token real, and does a fallback
		// contradict it?
		const varRe = new RegExp(`var\\(\\s*(${TOKEN_RE})\\s*,([^()]*?)\\)`, 'g');
		let varMatch: RegExpExecArray | null;
		while ((varMatch = varRe.exec(css)) !== null) {
			const token = varMatch[1];
			const fallback = varMatch[2].trim();
			const declared = palette[token];
			if (declared === undefined) {
				if (!assignedTokens.has(token)) {
					findings.push({ kind: 'dead-token', path: source.path, token, fallback });
				}
			} else if (!sameValue(declared, fallback)) {
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
			if (!foreground || !background) continue;

			const fg = resolveColorValue(foreground, palette);
			const bg = resolveColorValue(background, palette);
			if (!fg || !bg) continue;

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

/** Compare two CSS values for "the same colour", so `#FFF` === `white`. */
function sameValue(a: string, b: string): boolean {
	const normalize = (v: string) => {
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
	}
}
