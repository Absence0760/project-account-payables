/**
 * Static audit of the checkbox **target-size** recipe (WCAG 2.2 AA, SC 2.5.8).
 *
 * The criterion asks for a 24×24 CSS-px target, or — through its *spacing*
 * exception — an undersized target whose 24px-diameter circle reaches no other
 * target. The app's checkbox is painted 16×16, so it used to clear 2.5.8 only
 * through that exception, and whether it did was decided by whatever padding a
 * page gave its checkbox column: `/invoices` (`width: 36px`, 10px/4px padding)
 * left 7px of clear space, `/exceptions` (`width: 32px; padding-right: 0`)
 * left 2px. `app.css` now meets the SIZE half outright, with a recipe that has
 * to hold in three places at once:
 *
 *   • `min-width` / `min-height: 24px` — the floor, and the property that
 *     carries the criterion. It is spelled `min-*` rather than `width`/`height`
 *     precisely so a page-scoped `input { … }` rule cannot shrink it back.
 *   • `border: 4px solid transparent` + `background-clip: padding-box` — the
 *     8px of hit area that is added without painting anything, so the control
 *     still *looks* 16×16.
 *   • `margin: -4px` — hands those 8px back to the layout, so the margin box
 *     (what a line box, a flex row or a table cell measures) is still 16×16 and
 *     no row grows.
 *
 * Remove any one of those and the fix silently degrades: drop `min-*` and a
 * page rule shrinks the target; drop the transparent border and the control is
 * painted 24×24; drop the negative margin and every table row grows 8px. None
 * of those is visible in a diff of one file, which is what this scan is for.
 *
 * **Scope note — Svelte scoping is what makes this precise.** A component's
 * `<style>` only reaches that component's own markup, so a generic
 * `input { border: 1px … }` rule in a file that renders no checkbox cannot
 * affect one. Callers therefore filter sources with {@link fileCanStyleCheckbox}
 * before scanning; without that filter the scan reports every text-field
 * recipe in the tree and means nothing.
 *
 * Everything here is pure: callers hand in already-read sources, the same
 * contract `cssAudit` / `opacityAudit` use, so the repo-wide guard
 * (`targetSizeAudit.test.ts`) is the file walk plus an assertion.
 */

import { parseRules, type StyleSource } from './cssAudit';

/** The SC 2.5.8 minimum, in CSS px. */
export const MIN_TARGET_PX = 24;

/** The base rule this module is the guard for. */
export const CHECKBOX_BASE_SELECTOR = "input[type='checkbox']";

export interface BaseRuleFinding {
	kind: 'base-rule';
	/** The declaration that is missing or wrong. */
	property: string;
	/** What the target-size recipe requires. */
	expected: string;
	/** What the rule says, or `null` when it says nothing. */
	actual: string | null;
}

export interface OverrideFinding {
	/**
	 * `shrinks-target` — a rule that can take the hit box back under 24px, which
	 * breaks the criterion. `reshapes-control` — a rule that re-declares the
	 * transparent border, the background or the background clip: 2.5.8 still
	 * holds (the `min-*` floor is untouched) but the control is repainted as a
	 * bordered box the size of the hit area, and a `background` shorthand also
	 * wipes the drawn tick.
	 */
	kind: 'shrinks-target' | 'reshapes-control';
	path: string;
	selector: string;
	/** The offending declaration, as written. */
	declaration: string;
}

export interface CheckedPaintFinding {
	kind: 'checked-paint';
	/** The declaration that is missing or no longer paints what it should. */
	property: string;
	expected: string;
	/** The merged value across the `:checked` rules, or `null` when unset. */
	actual: string | null;
}

export type TargetSizeFinding = BaseRuleFinding | OverrideFinding | CheckedPaintFinding;

/** Stable `path {selector}` identity, matching `opacityAudit`'s key shape. */
export function findingKey(finding: OverrideFinding): string {
	return `${finding.path} {${finding.selector}}`;
}

const normalise = (selector: string) => selector.replace(/["']/g, '').replace(/\s+/g, ' ').trim();

/**
 * Could this selector reach an `<input type="checkbox">`?
 *
 * Three cases, in order: an explicit `:not([type=checkbox])` carve-out (the one
 * `app.css`'s `.modal input` recipe and `routes/admin/privacy` both spell) is a
 * deliberate exclusion; an explicit `[type=checkbox]` is a hit; and a bare
 * `input` term with no type filter is a hit, because that is the shape of every
 * page's text-field recipe and it lands on checkboxes too.
 */
export function selectorCanMatchCheckbox(selector: string): boolean {
	return splitSelectorList(selector).some((one) => {
		const n = normalise(one);
		if (/:not\([^)]*type=checkbox[^)]*\)/.test(n)) return false;
		if (/\[type=checkbox\]/.test(n)) return true;
		// A bare `input`, i.e. one not immediately qualified by an attribute
		// selector — `input[type=text]` cannot match a checkbox.
		return /(^|[\s>+~])input(?![\w[-])/.test(n);
	});
}

/** Split a selector list on top-level commas (a `:not(a, b)` is not a split). */
function splitSelectorList(selector: string): string[] {
	const out: string[] = [];
	let depth = 0;
	let current = '';
	for (const ch of selector) {
		if (ch === '(') depth++;
		else if (ch === ')') depth--;
		if (ch === ',' && depth === 0) {
			out.push(current);
			current = '';
		} else current += ch;
	}
	out.push(current);
	return out.map((s) => s.trim()).filter(Boolean);
}

/**
 * Does this FILE render a checkbox that its own `<style>` could reach?
 *
 * `app.css` is global, so it always can. A `.svelte` file's styles are scoped
 * to its own markup, so the question is whether that markup contains a
 * checkbox. Takes the raw file source (a markup question, not a CSS one).
 */
export function fileCanStyleCheckbox(path: string, source: string): boolean {
	if (path.endsWith('.css')) return true;
	return /type=["']checkbox["']/.test(source);
}

/** `prop: value` pairs of the checkbox base rule, or `null` when it is gone. */
export function findCheckboxBaseRule(sources: StyleSource[]): [string, string][] | null {
	for (const source of sources) {
		for (const rule of parseRules(source.css)) {
			if (normalise(rule.selector) === normalise(CHECKBOX_BASE_SELECTOR)) return rule.declarations;
		}
	}
	return null;
}

/**
 * A CSS length in px, or `null` when this scan cannot say.
 *
 * `px` and a unitless `0` (a valid zero length, and the spelling every
 * `min-width: 0` in this tree uses) resolve exactly; `rem` resolves against the
 * 16px root, which the app never changes. `em` and percentages depend on
 * context the scan cannot resolve, so they come back `null` and callers treat
 * that as "not a finding" rather than guessing.
 */
const px = (value: string): number | null => {
	const v = value.trim();
	if (/^-?0$/.test(v)) return 0;
	const m = /^(-?\d+(?:\.\d+)?)(px|rem)$/.exec(v);
	if (!m) return null;
	return m[2] === 'rem' ? Number(m[1]) * 16 : Number(m[1]);
};

/**
 * Every declaration the recipe rests on, with what it is for. `ok` answers
 * "does this value still do that job?" — a value that does is not a finding.
 */
const BASE_REQUIREMENTS: {
	property: string;
	expected: string;
	ok: (value: string) => boolean;
}[] = [
	{
		property: 'min-width',
		expected: `>= ${MIN_TARGET_PX}px`,
		ok: (v) => (px(v) ?? -1) >= MIN_TARGET_PX
	},
	{
		property: 'min-height',
		expected: `>= ${MIN_TARGET_PX}px`,
		ok: (v) => (px(v) ?? -1) >= MIN_TARGET_PX
	},
	{
		// The hit area itself: 4px on every side takes a 16px painted box to a
		// 24px target. A visible colour here would paint it instead of adding it.
		property: 'border',
		expected: '4px solid transparent',
		ok: (v) => /(^|\s)4px(\s|$)/.test(v) && /transparent/.test(v)
	},
	{
		// Without this the fill and the tick would spread into the border band
		// and the control would look 24×24.
		property: 'background-clip',
		expected: 'padding-box',
		ok: (v) => v.trim() === 'padding-box'
	},
	{
		// The layout half: the margin box stays 16×16, so no row moves.
		property: 'margin',
		expected: '-4px',
		ok: (v) => v.trim() === '-4px'
	}
];

/** Findings against the base rule itself. Must be empty. */
export function auditCheckboxBaseRule(sources: StyleSource[]): BaseRuleFinding[] {
	const declarations = findCheckboxBaseRule(sources);
	if (!declarations) {
		return [
			{
				kind: 'base-rule',
				property: CHECKBOX_BASE_SELECTOR,
				expected: 'a base rule in app.css',
				actual: null
			}
		];
	}
	const findings: BaseRuleFinding[] = [];
	for (const requirement of BASE_REQUIREMENTS) {
		// Last declaration wins, as in the cascade.
		const declared = declarations.filter(([name]) => name === requirement.property).pop();
		if (!declared || !requirement.ok(declared[1])) {
			findings.push({
				kind: 'base-rule',
				property: requirement.property,
				expected: requirement.expected,
				actual: declared ? declared[1] : null
			});
		}
	}
	return findings;
}

/**
 * Declarations that repaint the control rather than resize its target.
 *
 * `border` / `border-width` replace the transparent hit-area band with a
 * visible one, so the box is painted at the full 24×24. The `background`
 * SHORTHAND is here and `background-color` is not, because the shorthand
 * resets `background-clip` (so the fill spreads into the border band) AND
 * `background-image` — which is the drawn tick, so a CHECKED checkbox under
 * such a rule renders indistinguishably from an unchecked one.
 */
const RESHAPING_PROPERTIES = new Set(['border', 'border-width', 'background', 'background-clip']);

/** Scale factors below 1 shrink the rendered — and hit-tested — box. */
function shrinkingScale(value: string): boolean {
	const re = /scale[xy]?\(\s*(-?\d*\.?\d+)/gi;
	let m: RegExpExecArray | null;
	while ((m = re.exec(value)) !== null) {
		if (Math.abs(Number(m[1])) < 1) return true;
	}
	return false;
}

/**
 * Rules elsewhere in the tree that can reach a checkbox and either shrink its
 * target or repaint its box. Sources should already be filtered with
 * {@link fileCanStyleCheckbox}.
 */
export function findCheckboxOverrides(sources: StyleSource[]): OverrideFinding[] {
	const findings: OverrideFinding[] = [];
	for (const source of sources) {
		for (const rule of parseRules(source.css)) {
			const selector = normalise(rule.selector);
			if (selector === normalise(CHECKBOX_BASE_SELECTOR)) continue;
			if (!selectorCanMatchCheckbox(rule.selector)) continue;
			for (const [property, value] of rule.declarations) {
				const declaration = `${property}: ${value}`;
				if (
					(property === 'min-width' || property === 'min-height') &&
					(px(value) ?? MIN_TARGET_PX) < MIN_TARGET_PX
				) {
					findings.push({ kind: 'shrinks-target', path: source.path, selector, declaration });
				} else if (property === 'transform' && shrinkingScale(value)) {
					findings.push({ kind: 'shrinks-target', path: source.path, selector, declaration });
				} else if (RESHAPING_PROPERTIES.has(property)) {
					findings.push({ kind: 'reshapes-control', path: source.path, selector, declaration });
				}
			}
		}
	}
	return findings;
}

/** The compound whose painted result decides whether a tick is visible. */
const CHECKED_SELECTOR = "input[type='checkbox']:checked";

/**
 * What a CHECKED checkbox actually resolves to — the property that was wrong,
 * not the specificity that caused it.
 *
 * The defect this closes was never a layout bug: five dialogs' text-field
 * recipes reached their own checkboxes and spelled the fill as the
 * `background` SHORTHAND, which resets `background-image`. The drawn tick IS
 * that background image, so "Auto renew", "Reimbursable" and "Active"
 * rendered pixel-identical checked and unchecked — a user could not tell what
 * they were about to save. Nothing in a layout or contrast check sees that.
 *
 * Two halves make the claim, and both are needed. This function is the
 * positive one: the `:checked` rules DECLARE a tick and an accent ring.
 * {@link findCheckboxOverrides} is the negative one: no rule that can reach a
 * checkbox re-declares `background` / `border`, so nothing can reset them.
 * Asserted statically rather than through a resolved `getComputedStyle`
 * because jsdom does not implement cascade resolution faithfully enough to
 * trust with a correctness claim, and a browser is the e2e guard's job.
 *
 * The ring is deliberately checked mechanism-agnostically: it is an inset
 * `box-shadow` today (the real border is now the transparent hit area) and was
 * a `border-color` before, and the question worth guarding is whether the
 * checked state is drawn in the accent at all.
 */
export function auditCheckedCheckboxPaint(sources: StyleSource[]): CheckedPaintFinding[] {
	const merged = new Map<string, string>();
	for (const source of sources) {
		for (const rule of parseRules(source.css)) {
			const matches = splitSelectorList(rule.selector).some(
				(one) => normalise(one) === normalise(CHECKED_SELECTOR)
			);
			if (!matches) continue;
			// Later declarations win, as in the cascade.
			for (const [property, value] of rule.declarations) merged.set(property, value);
		}
	}

	const findings: CheckedPaintFinding[] = [];
	const need = (property: string, expected: string, ok: (v: string) => boolean) => {
		const value = merged.get(property) ?? null;
		if (value === null || !ok(value)) findings.push({ kind: 'checked-paint', property, expected, actual: value });
	};

	// The tick. `none` or absent is the exact symptom the shorthand produced.
	need('background-image', 'a drawn mark (url(…)), never none', (v) => /\burl\(/.test(v) && !/^none$/i.test(v.trim()));
	// The accent fill behind it.
	need('background-color', 'var(--accent)', (v) => /var\(\s*--accent\s*\)/.test(v));

	// …and the accent ring, by whichever mechanism draws it.
	const ring = merged.get('box-shadow') ?? merged.get('border-color') ?? null;
	if (!ring || !/var\(\s*--accent\s*\)/.test(ring)) {
		findings.push({
			kind: 'checked-paint',
			property: 'box-shadow | border-color',
			expected: 'an accent ring — var(--accent)',
			actual: ring
		});
	}
	return findings;
}
