import { describe, expect, it } from 'vitest';
import {
	auditStyles,
	collectAssignedTokens,
	describeFinding,
	extractStyleBlocks,
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
});

describe('parsePalette', () => {
	it('reads the :root token block', () => {
		expect(parsePalette(':root{--bg:#0f1117;--text:#e2e4ea;font-family:sans-serif}')).toEqual({
			'--bg': '#0f1117',
			'--text': '#e2e4ea'
		});
	});
});
