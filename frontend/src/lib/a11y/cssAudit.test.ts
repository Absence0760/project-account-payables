import { describe, expect, it } from 'vitest';
import {
	auditStyles,
	collectAssignedTokens,
	describeFinding,
	extractStyleBlocks,
	findVarReferences,
	isLargeText,
	parseDeclarations,
	parsePalette,
	parseRules,
	resolveColorValue,
	stripCssComments,
	type StyleFinding
} from './cssAudit';

const PALETTE = {
	'--bg': '#0f1117',
	'--surface-2': '#232b44',
	'--text': '#e2e4ea',
	'--text-muted': '#8a8fa0',
	'--accent': '#638cff',
	'--accent-strong': '#3f5fd6'
};

const audit = (css: string, assigned: string[] = []) =>
	auditStyles([{ path: 'fixture.css', css }], {
		palette: PALETTE,
		assignedTokens: new Set(assigned)
	});

/** The same audit with the bare-literal rule armed, as the repo guard runs it. */
const auditText = (css: string) =>
	auditStyles([{ path: 'fixture.css', css }], {
		palette: PALETTE,
		assignedTokens: new Set(),
		textSurfaces: ['--bg', '--surface']
	});

describe('stripCssComments', () => {
	it('removes block comments, including multi-line ones', () => {
		expect(stripCssComments('a{/* x\n y */color:red}')).toBe('a{color:red}');
	});

	/**
	 * app.css documents the failing pairs in prose right next to the tokens.
	 * If comments weren't stripped, the guard would flag its own explanation.
	 */
	it('keeps prose about a failing pair from parsing as CSS', () => {
		const css = `:root{ /* --text-muted on --surface-2 is 4.34:1 */ --text: #e2e4ea; }`;
		expect(parsePalette(css)).toEqual({ '--text': '#e2e4ea' });
	});
});

describe('extractStyleBlocks', () => {
	it('returns a .css file whole', () => {
		expect(extractStyleBlocks('app.css', 'a{color:red}')).toEqual([
			{ path: 'app.css', css: 'a{color:red}' }
		]);
	});

	it('returns every <style> block of a .svelte file and nothing else', () => {
		const source = `<div class="x">not css</div>\n<style>.a{color:red}</style>\n<style module>.b{color:blue}</style>`;
		expect(extractStyleBlocks('X.svelte', source).map((b) => b.css)).toEqual([
			'.a{color:red}',
			'.b{color:blue}'
		]);
	});

	it('returns nothing for a component with no styles', () => {
		expect(extractStyleBlocks('X.svelte', '<p>hi</p>')).toEqual([]);
	});
});

describe('parseDeclarations', () => {
	it('keeps a semicolon inside parentheses out of the split', () => {
		expect(parseDeclarations('background: url("a;b"); color: red')).toEqual([
			['background', 'url("a;b")'],
			['color', 'red']
		]);
	});

	it('preserves source order and lowercases only the property', () => {
		expect(parseDeclarations('Color: #FFF; color: #000')).toEqual([
			['color', '#FFF'],
			['color', '#000']
		]);
	});
});

describe('parseRules', () => {
	it('finds the leaf rule inside a media query', () => {
		const rules = parseRules('@media (max-width: 900px) { .a { color: red; background: blue } }');
		expect(rules).toHaveLength(1);
		expect(rules[0].selector).toBe('.a');
	});

	it('collapses whitespace in a multi-line selector list', () => {
		const rules = parseRules('.a,\n\t.b {\n color: red\n}');
		expect(rules[0].selector).toBe('.a, .b');
	});
});

describe('collectAssignedTokens', () => {
	it('finds a token assigned in an inline style attribute', () => {
		expect(collectAssignedTokens('<li style="--type-color: #f00">x</li>')).toEqual([
			'--type-color'
		]);
	});

	it('does not treat a var() *reference* as an assignment', () => {
		expect(collectAssignedTokens('.a{ color: var(--accent, red) }')).toEqual([]);
	});
});

describe('resolveColorValue', () => {
	it('follows a var() to its palette value', () => {
		expect(resolveColorValue('var(--accent)', PALETTE)).toBe('#638cff');
	});

	it('prefers the declared token over a fallback', () => {
		expect(resolveColorValue('var(--accent, #ff0000)', PALETTE)).toBe('#638cff');
	});

	it('falls back only when the token is undefined', () => {
		expect(resolveColorValue('var(--nope, #ff0000)', PALETTE)).toBe('#ff0000');
		expect(resolveColorValue('var(--nope)', PALETTE)).toBeNull();
	});

	it('pulls the colour out of a background shorthand', () => {
		expect(resolveColorValue('var(--bg) url(x.png) no-repeat', PALETTE)).toBe('#0f1117');
	});

	it('refuses a gradient — there is no single background colour', () => {
		expect(resolveColorValue('linear-gradient(var(--bg), var(--accent))', PALETTE)).toBeNull();
	});

	it('refuses a translucent value', () => {
		expect(resolveColorValue('rgba(0, 0, 0, 0.4)', PALETTE)).toBeNull();
	});
});

describe('isLargeText', () => {
	it('treats 24px+ as large at any weight', () => {
		expect(isLargeText([['font-size', '1.5rem']])).toBe(true);
		expect(isLargeText([['font-size', '24px']])).toBe(true);
	});

	it('treats 18.66px+ as large only when bold', () => {
		expect(isLargeText([['font-size', '1.25rem']])).toBe(false);
		expect(
			isLargeText([
				['font-size', '1.25rem'],
				['font-weight', '700']
			])
		).toBe(true);
	});

	/** An em/percent/calc size is relative to something we can't see here. */
	it('treats an unresolvable size as normal text', () => {
		expect(isLargeText([['font-size', '2em']])).toBe(false);
		expect(isLargeText([['font-size', 'calc(1rem + 2px)']])).toBe(false);
		expect(isLargeText([])).toBe(false);
	});
});

describe('auditStyles — contrast', () => {
	it('flags the --text-muted on --surface-2 pair at 4.34:1', () => {
		const findings = audit('.chip{background:var(--surface-2);color:var(--text-muted)}');
		expect(findings).toHaveLength(1);
		const f = findings[0] as Extract<StyleFinding, { kind: 'contrast' }>;
		expect(f.kind).toBe('contrast');
		expect(f.ratio).toBeCloseTo(4.34, 2);
		expect(f.required).toBe(4.5);
		expect(describeFinding(f)).toContain('below the 4.5:1 bar');
	});

	it('passes the same surface with --text', () => {
		expect(audit('.chip{background:var(--surface-2);color:var(--text)}')).toEqual([]);
	});

	it('flags white on --accent but not on --accent-strong', () => {
		expect(audit('.btn{background:var(--accent);color:#fff}')).toHaveLength(1);
		expect(audit('.btn{background:var(--accent-strong);color:#fff}')).toEqual([]);
	});

	it('applies the 3:1 large-text bar when the rule declares a large size', () => {
		expect(
			audit('.h{background:var(--accent);color:#fff;font-size:1.5rem}')
		).toEqual([]);
	});

	it('ignores a rule that sets only one side — the cascade is axe’s job', () => {
		expect(audit('.a{color:var(--text-muted)}')).toEqual([]);
		expect(audit('.a{background:var(--surface-2)}')).toEqual([]);
	});

	it('honours later declarations winning', () => {
		// The failing background is overwritten by a passing one.
		expect(
			audit('.a{background:var(--accent);background:var(--accent-strong);color:#fff}')
		).toEqual([]);
	});

	it('skips a pair it cannot resolve rather than guessing', () => {
		expect(audit('.a{background:linear-gradient(#fff,#000);color:#fff}')).toEqual([]);
		expect(audit('.a{background:rgba(0,0,0,.4);color:#fff}')).toEqual([]);
	});
});

describe('auditStyles — token drift', () => {
	it('flags a fallback that contradicts the declared token', () => {
		const findings = audit('.a{color:var(--text-muted, #94a3b8)}');
		expect(findings).toHaveLength(1);
		expect(findings[0]).toMatchObject({
			kind: 'stale-fallback',
			token: '--text-muted',
			fallback: '#94a3b8',
			declared: '#8a8fa0'
		});
	});

	it('accepts a fallback that agrees with the token, however it is spelled', () => {
		expect(audit('.a{color:var(--text-muted, #8A8FA0)}')).toEqual([]);
	});

	it('flags a var() whose token nothing ever assigns', () => {
		const findings = audit('.a{color:var(--danger, #f87171)}');
		expect(findings).toHaveLength(1);
		expect(findings[0]).toMatchObject({ kind: 'dead-token', token: '--danger' });
		expect(describeFinding(findings[0])).toContain('the fallback is what always renders');
	});

	it('does not flag a token assigned per-element rather than in :root', () => {
		expect(audit('.a{border-color:var(--type-color, #fff)}', ['--type-color'])).toEqual([]);
	});

	it('says nothing about a var() with no fallback at all', () => {
		expect(audit('.a{color:var(--text-muted)}')).toEqual([]);
	});

	/**
	 * A fallback can itself be a `var()`. A `[^()]*` capture matches nothing
	 * there, which would have been a hole in the exact guard this module is —
	 * `var(--bg, var(--surface))` shipped in two routes and was invisible to
	 * both drift checks.
	 */
	it('sees through a fallback that is itself a var()', () => {
		const findings = audit('.a{background:var(--bg, var(--surface))}');
		expect(findings).toHaveLength(1);
		expect(findings[0]).toMatchObject({ kind: 'stale-fallback', token: '--bg' });
	});

	it('compares a token-valued fallback by the colour it resolves to', () => {
		// --accent and its fallback spell the same colour two ways: not stale.
		expect(audit('.a{color:var(--accent, var(--accent))}')).toEqual([]);
	});

	it('still exempts a locally-assigned token behind a nested fallback', () => {
		expect(
			audit('.a{border-color:var(--type-color, var(--accent))}', ['--type-color'])
		).toEqual([]);
	});
});

describe('findVarReferences', () => {
	it('captures a plain reference, with and without a fallback', () => {
		expect(findVarReferences('color: var(--a); background: var(--b, #fff)')).toEqual([
			{ token: '--a', fallback: null },
			{ token: '--b', fallback: '#fff' }
		]);
	});

	it('captures a nested var() fallback whole, and visits the inner one too', () => {
		expect(findVarReferences('background: var(--bg, var(--surface))')).toEqual([
			{ token: '--bg', fallback: 'var(--surface)' },
			{ token: '--surface', fallback: null }
		]);
	});

	it('splits on the FIRST top-level comma, so a multi-part fallback survives', () => {
		expect(findVarReferences('font-family: var(--mono, ui-monospace, monospace)')).toEqual([
			{ token: '--mono', fallback: 'ui-monospace, monospace' }
		]);
	});

	it('keeps a function call in the fallback intact', () => {
		expect(findVarReferences('color: var(--x, rgba(0, 0, 0, 1))')).toEqual([
			{ token: '--x', fallback: 'rgba(0, 0, 0, 1)' }
		]);
	});

	it('ignores an unbalanced var( rather than guessing where it ends', () => {
		expect(findVarReferences('color: var(--x, #fff')).toEqual([]);
	});
});

describe('auditStyles — bare literal text colour', () => {
	/**
	 * The class the same-rule pair check structurally cannot see: the rule
	 * sets only `color`, so the background arrives through the cascade. The
	 * sound question left is whether the literal is legible on the surfaces
	 * body text renders on.
	 */
	it('flags a literal below the bar on an app surface', () => {
		// #e04040 — 4.47:1 on --bg, 4.11:1 on --surface.
		const findings = auditText('.err{color:#e04040}');
		expect(findings).toHaveLength(1);
		expect(findings[0]).toMatchObject({
			kind: 'literal-text-color',
			colorValue: '#e04040',
			surface: '--bg'
		});
		expect(describeFinding(findings[0])).toContain('use a palette token');
	});

	it('reports one finding per rule even when several surfaces fail', () => {
		expect(auditText('.err{color:#e04040}')).toHaveLength(1);
	});

	it('passes a literal that clears the bar everywhere', () => {
		// #f87171 — 6.82:1 on --bg, 6.27:1 on --surface.
		expect(auditText('.err{color:#f87171}')).toEqual([]);
	});

	/** A token is exempt: `palette contract` asserts it against these surfaces. */
	it('never flags a palette token', () => {
		expect(auditText('.a{color:var(--text-muted)}')).toEqual([]);
	});

	/**
	 * White and black are the deliberate on-a-coloured-fill choices; their
	 * background legitimately comes from a parent rule the scanner can't see.
	 */
	it('exempts white and black', () => {
		expect(auditText('.a{color:#fff}')).toEqual([]);
		expect(auditText('.a{color:white}')).toEqual([]);
		expect(auditText('.a{color:#000}')).toEqual([]);
	});

	it('stands down once the rule declares its own OPAQUE background — the pair check owns it', () => {
		// Same failing literal, but now on a stated background: reported as a
		// contrast pair (against #fff), not as a bare literal.
		const findings = auditText('.a{color:#e04040;background:#fff}');
		expect(findings.map((f) => f.kind)).toEqual(['contrast']);
	});

	/**
	 * A translucent tint composites against whatever is behind it, so the pair
	 * check can't judge it — and it's the standard dark-theme status-pill
	 * shape. Falling silent there is how the purple/green/amber pill text kept
	 * its sub-4.5:1 colours; holding the literal to the bare surface is the
	 * right approximation and the conservative one.
	 */
	it('still applies when the declared background is translucent or a gradient', () => {
		const tint = auditText('.badge{background:rgba(224,64,64,0.15);color:#e04040}');
		expect(tint.map((f) => f.kind)).toEqual(['literal-text-color']);
		const gradient = auditText('.g{background:linear-gradient(#000,#fff);color:#e04040}');
		expect(gradient.map((f) => f.kind)).toEqual(['literal-text-color']);
	});

	it('honours the large-text bar', () => {
		// 4.47:1 clears the 3:1 large-text threshold.
		expect(auditText('.h{color:#e04040;font-size:1.5rem}')).toEqual([]);
	});

	it('does nothing when no text surfaces are configured', () => {
		expect(audit('.err{color:#e04040}')).toEqual([]);
	});
});

describe('parsePalette', () => {
	it('reads the :root token block', () => {
		expect(parsePalette(':root{--bg:#0f1117;--text:#e2e4ea;font-family:sans-serif}')).toEqual({
			'--bg': '#0f1117',
			'--text': '#e2e4ea'
		});
	});
});
