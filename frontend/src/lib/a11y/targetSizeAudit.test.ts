import { describe, expect, it } from 'vitest';
import {
	auditCheckboxBaseRule,
	auditCheckedCheckboxPaint,
	CHECKBOX_BASE_SELECTOR,
	fileCanStyleCheckbox,
	findCheckboxBaseRule,
	findCheckboxOverrides,
	findingKey,
	MIN_TARGET_PX,
	selectorCanMatchCheckbox
} from './targetSizeAudit';
import { extractStyleBlocks, type StyleSource } from './cssAudit';

/**
 * Repo-wide guard on the checkbox **target-size** recipe (WCAG 2.2 AA, 2.5.8).
 *
 * `input[type=checkbox]` is painted 16×16, under the criterion's 24×24 floor,
 * so it used to conform only through the *spacing* exception — and whether it
 * did was decided by each page's checkbox-column padding rather than by the
 * design system: `/invoices` left 7px of clear space around the box,
 * `/exceptions` left 2px. `app.css` now meets the size half outright, by
 * growing the element's own box to 24×24 (a 4px transparent border +
 * `background-clip: padding-box`) and giving the 8px straight back to the
 * layout (`margin: -4px`), so the painted control and every row height are
 * unchanged.
 *
 * That recipe is four declarations that only work together, in one rule, and
 * removing any of them degrades it in a way no diff review would flag:
 *
 *     drop `min-*`             → a page's `input { … }` rule shrinks the target
 *     drop the 4px transparent → the control is painted 24×24
 *     drop `background-clip`   → the fill and tick spread into the hit band
 *     drop `margin: -4px`      → every table row grows 8px
 *
 * **Why this exists next to the e2e guard.** `tests-e2e/a11y/target-size.spec.ts`
 * measures what a listed route renders, which is the only way to prove the
 * browser really hit-tests the enlarged box — but it can only see the pages in
 * its list, and only in the states those pages happen to reach. This scan reads
 * the stylesheets, so it catches the base rule being weakened for the whole app
 * from a file no route list covers. Neither subsumes the other; that is the
 * same split `opacityAudit` documents against `axe.spec.ts`.
 */

const RAW = import.meta.glob('/src/**/*.{svelte,css}', {
	query: '?raw',
	import: 'default',
	eager: true
}) as Record<string, string>;

const files = Object.entries(RAW)
	.map(([path, source]) => [path.replace(/^\/src\//, ''), source] as const)
	.sort(([a], [b]) => a.localeCompare(b));

/** Everything, for the base-rule check — the rule lives in `app.css`. */
const allSources: StyleSource[] = files.flatMap(([path, source]) =>
	extractStyleBlocks(path, source)
);

/**
 * Only the stylesheets that can actually reach a checkbox. Svelte scopes a
 * component's `<style>` to that component's own markup, so a generic
 * `input { border: 1px … }` recipe in a file rendering no checkbox is
 * irrelevant here — including it would drown the scan in text fields.
 */
const checkboxSources: StyleSource[] = files
	.filter(([path, source]) => fileCanStyleCheckbox(path, source))
	.flatMap(([path, source]) => extractStyleBlocks(path, source));

/**
 * Files whose own generic text-field recipe still lands on their own
 * checkboxes. **Empty, and it should stay that way.**
 *
 * It was not empty. Twelve rules — `.form-grid input` in four modals, a bare
 * `input` recipe on `/organization` and `/profile`, and six more — were
 * written for text fields and reached checkboxes too, at a specificity the
 * global base cannot outrank (Svelte compiles `.form-grid input` to
 * `.form-grid.svelte-x input:where(.svelte-x)`, i.e. 0-2-1 against the base's
 * 0-1-1). Each spelled its fill as the `background` SHORTHAND, which resets
 * `background-image` — and the drawn tick IS that image, so "Auto renew",
 * "Not to exceed", "Reimbursable" and "Active" rendered pixel-identical
 * checked and unchecked. Two of them also spelled `outline: none` on `:focus`,
 * which stripped the checkbox focus ring (WCAG 2.4.7).
 *
 * All twelve now carry the `:not([type='checkbox']):not([type='radio'])`
 * carve-out that `app.css`'s `.modal input` recipe and `routes/admin/privacy`
 * already spelled, so this map is empty and the check below is a hard zero.
 *
 * **Only ever remove entries.** The shape stays because it is the contract: a
 * conversion that genuinely has to be tranched records itself here with its
 * reason, exactly as `opacityAudit`'s `PENDING_CONVERSION` does, rather than
 * being argued for in a commit message. Adding an entry is not a way to land
 * a new one.
 */
const GENERIC_INPUT_RECIPE_REACHES_CHECKBOX: Record<string, string> = {};

/**
 * Files a carve-out took to zero. A generic input recipe reappearing in one of
 * these fails by name rather than as an anonymous count — six of them are
 * dialogs whose only symptom was a tick that stopped being drawn, which is
 * precisely the kind of regression nobody notices in review.
 */
const CONVERTED = [
	'app.css',
	'lib/components/modals/BulkRecodeGLModal.svelte',
	'lib/components/modals/CatalogModal.svelte',
	'lib/components/modals/ContractModal.svelte',
	'lib/components/modals/ExpenseModal.svelte',
	'lib/components/modals/PolicyModal.svelte',
	'routes/expenses/+page.svelte',
	'routes/organization/+page.svelte',
	'routes/profile/+page.svelte',
	'routes/workflows/+page.svelte'
];

const baseFindings = auditCheckboxBaseRule(allSources);
const paintFindings = auditCheckedCheckboxPaint(allSources);
const overrides = findCheckboxOverrides(checkboxSources);

describe('targetSizeAudit — the scanner', () => {
	const scan = (css: string) => findCheckboxOverrides([{ path: 'fixture.css', css }]);

	it('recognises the selectors that can reach a checkbox', () => {
		expect(selectorCanMatchCheckbox("input[type='checkbox']")).toBe(true);
		expect(selectorCanMatchCheckbox('.form-grid input')).toBe(true);
		expect(selectorCanMatchCheckbox('input, select, textarea')).toBe(true);
		expect(selectorCanMatchCheckbox('.checkbox-col input[type="checkbox"]')).toBe(true);
	});

	it('respects an explicit checkbox carve-out and a typed input', () => {
		// The two spellings already in the tree — `app.css`'s `.modal input`
		// recipe and `routes/admin/privacy`. A carve-out is the fix for every
		// entry in the ratchet map, so mistaking one for a hit would make the
		// guard un-clearable.
		expect(
			selectorCanMatchCheckbox(".modal input:not([type='checkbox']):not([type='radio'])")
		).toBe(false);
		expect(selectorCanMatchCheckbox("input[type='text']")).toBe(false);
		expect(selectorCanMatchCheckbox("input[type='file']::file-selector-button")).toBe(false);
		expect(selectorCanMatchCheckbox('.btn, .card')).toBe(false);
	});

	it('flags a rule that shrinks the target below the 2.5.8 floor', () => {
		expect(scan('.form-grid input { min-width: 0; }')).toEqual([
			{
				kind: 'shrinks-target',
				path: 'fixture.css',
				selector: '.form-grid input',
				declaration: 'min-width: 0'
			}
		]);
		expect(scan('.dense input { transform: scale(0.8); }')[0].kind).toBe('shrinks-target');
		// A value at or above the floor is not a finding.
		expect(scan(`.wide input { min-width: ${MIN_TARGET_PX}px; }`)).toEqual([]);
		expect(scan('.zoom input { transform: scale(1.25); }')).toEqual([]);
	});

	it('flags a text-field recipe that repaints the control', () => {
		const found = scan('.form-grid input { border: 1px solid #333; background: #000; }');
		expect(found.map((f) => f.kind)).toEqual(['reshapes-control', 'reshapes-control']);
		// `background-color` is NOT a finding: it sets the fill without resetting
		// the clip or the tick, which is what the base rule itself uses.
		expect(scan('.x input { background-color: #000; }')).toEqual([]);
	});

	it('catches the shorthand that erased the tick', () => {
		// The exact defect, as a fixture: a `:checked` rule whose fill is the
		// `background` SHORTHAND declares no `background-image`, so the drawn
		// mark is gone and a checked box paints like an unchecked one.
		const shorthandOnly = auditCheckedCheckboxPaint([
			{
				path: 'fixture.css',
				css: "input[type='checkbox']:checked { background: var(--accent); }"
			}
		]);
		expect(shorthandOnly.map((f) => f.property)).toContain('background-image');

		// `background-image: none` is the same claim spelled explicitly.
		const explicitNone = auditCheckedCheckboxPaint([
			{
				path: 'fixture.css',
				css: "input[type='checkbox']:checked { background-image: none; }"
			}
		]);
		expect(explicitNone.map((f) => f.property)).toContain('background-image');
	});

	it('accepts a checked state that paints a tick, a fill and an accent ring', () => {
		expect(
			auditCheckedCheckboxPaint([
				{
					path: 'fixture.css',
					css:
						"input[type='checkbox']:checked, input[type='checkbox']:indeterminate {" +
						' background-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }' +
						" input[type='checkbox']:checked { background-image: url(\"data:image/svg+xml,x\"); }"
				}
			])
		).toEqual([]);
	});

	it('accepts either mechanism for the accent ring', () => {
		// It is an inset box-shadow now (the real border became the transparent
		// hit area) and was a border-color before. The question worth guarding
		// is whether the checked state is drawn in the accent at all.
		const withBorder = auditCheckedCheckboxPaint([
			{
				path: 'fixture.css',
				css:
					"input[type='checkbox']:checked { background-color: var(--accent);" +
					' border-color: var(--accent);' +
					' background-image: url("data:image/svg+xml,x"); }'
			}
		]);
		expect(withBorder).toEqual([]);
	});

	it('never reports the base rule against itself', () => {
		expect(
			scan(`${CHECKBOX_BASE_SELECTOR} { border: 4px solid transparent; background-clip: padding-box; }`)
		).toEqual([]);
	});

	it('scopes a component stylesheet to files that render a checkbox', () => {
		expect(fileCanStyleCheckbox('app.css', 'anything')).toBe(true);
		expect(fileCanStyleCheckbox('routes/x/+page.svelte', '<input type="checkbox" />')).toBe(true);
		expect(fileCanStyleCheckbox('routes/x/+page.svelte', '<input type="text" />')).toBe(false);
	});
});

describe('checkbox target-size recipe (WCAG 2.2 AA, 2.5.8)', () => {
	it('scans a non-trivial set of stylesheets', () => {
		// A glob that silently matched nothing would make every assertion below
		// pass vacuously.
		expect(allSources.length).toBeGreaterThan(50);
		expect(checkboxSources.length).toBeGreaterThan(5);
	});

	it('keeps every declaration the 24×24 hit box depends on', () => {
		expect(
			baseFindings,
			'The `input[type=checkbox]` base rule in app.css carries the whole SC ' +
				'2.5.8 recipe: `min-width`/`min-height: 24px` (the floor — spelled ' +
				'min-* so a page-scoped `width` cannot undo it), `border: 4px solid ' +
				'transparent` + `background-clip: padding-box` (hit area added ' +
				'without painting it), and `margin: -4px` (the layout gets the 8px ' +
				'back, so no row moves). Restore the missing declaration rather than ' +
				'relaxing this list.'
		).toEqual([]);
	});

	it('actually reaches the live stylesheet it is asserting about', () => {
		// The canary. Every assertion below is "we found nothing wrong", so a
		// parser that silently reached nothing would turn the whole suite green.
		// It used to be an offending rule on `/profile`; that rule is fixed, so
		// the canary is now the positive fact — the base rule was really parsed
		// out of `app.css` — which cannot go stale by being repaired.
		const base = findCheckboxBaseRule(allSources);
		expect(base).not.toBeNull();
		expect(base!.map(([property]) => property)).toEqual(
			expect.arrayContaining(['min-width', 'min-height', 'border', 'background-clip', 'margin'])
		);
	});

	it('paints a CHECKED checkbox with a tick, an accent fill and an accent ring', () => {
		// The property that was actually wrong. Five dialogs' text-field recipes
		// spelled their fill as the `background` shorthand, which resets
		// `background-image` — the drawn tick — so "Auto renew", "Reimbursable"
		// and "Active" rendered identically checked and unchecked. This asserts
		// the tick is declared; the two checks below assert nothing can reset it.
		expect(
			paintFindings,
			'The `:checked` rules in app.css must declare a drawn mark ' +
				'(`background-image: url(…)`), the accent fill and an accent ring. A ' +
				'checked checkbox that paints none of those is indistinguishable from ' +
				'an unchecked one, which is a correctness bug no contrast or layout ' +
				'check can see.'
		).toEqual([]);
	});

	it.each(CONVERTED)('%s keeps its generic input recipe off checkboxes', (path) => {
		expect(overrides.filter((f) => f.path === path).map(findingKey)).toEqual([]);
	});

	it('lets no rule shrink a checkbox back under the 24px floor', () => {
		expect(
			overrides.filter((f) => f.kind === 'shrinks-target').map(findingKey),
			'A `min-width`/`min-height` under 24px — or a `transform: scale()` ' +
				'below 1 — on a rule that can reach a checkbox takes the target back ' +
				'under the SC 2.5.8 floor, and no page-level styling need is worth ' +
				'that. Scope the rule to the inputs it means (`:not([type=checkbox])`).'
		).toEqual([]);
	});

	it('lets no generic input recipe repaint a checkbox', () => {
		const known = new Set(Object.keys(GENERIC_INPUT_RECIPE_REACHES_CHECKBOX));
		expect(
			overrides
				.filter((f) => f.kind === 'reshapes-control')
				.map(findingKey)
				.filter((key) => !known.has(key)),
			'A text-field recipe (`border`, or a `background` shorthand) written as ' +
				'`input { … }` / `.thing input { … }` also lands on that file\'s ' +
				'checkboxes, at a specificity the global base cannot outrank — so the ' +
				'control is painted as a bordered box, and the `background` shorthand ' +
				'resets the drawn tick, making a CHECKED checkbox look unchecked. Give ' +
				'the rule the `:not([type=\'checkbox\']):not([type=\'radio\'])` carve-out ' +
				'`app.css`\'s `.modal input` recipe already spells.'
		).toEqual([]);
	});

	it('has no stale entry in the ratchet map', () => {
		// An entry whose rule was fixed must be deleted, or the map slowly stops
		// describing the tree and stops failing on a genuine regression.
		const live = new Set(
			overrides.filter((f) => f.kind === 'reshapes-control').map(findingKey)
		);
		expect(
			Object.keys(GENERIC_INPUT_RECIPE_REACHES_CHECKBOX).filter((key) => !live.has(key)),
			'These rules no longer reach a checkbox — remove them from ' +
				'GENERIC_INPUT_RECIPE_REACHES_CHECKBOX in the same change that fixed them.'
		).toEqual([]);
	});
});
