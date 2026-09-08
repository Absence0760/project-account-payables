import { describe, expect, it } from 'vitest';
import {
	auditCheckboxBaseRule,
	CHECKBOX_BASE_SELECTOR,
	fileCanStyleCheckbox,
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
 * Files whose own generic text-field recipe lands on their own checkboxes,
 * with what it costs. **These are pre-existing, and predate the target-size
 * work** — the scan surfaces them, it did not cause them.
 *
 * Each of these rules is a `.form-grid input` / bare `input` recipe written for
 * text fields, at a specificity that outranks the global checkbox base. Two
 * consequences, and the second is the serious one:
 *
 *   1. the checkbox is painted as a bordered box the size of its hit area
 *      rather than the 16×16 control (measured 20×16 before this change, 24×24
 *      after — either way not the design-system control); and
 *   2. `background: var(--bg)` is a SHORTHAND, so it resets `background-image`
 *      — the drawn tick — which means a **checked checkbox renders identically
 *      to an unchecked one** in these dialogs ("1099 eligible", "Auto renew",
 *      "Not to exceed", "Reimbursable", "Active").
 *
 * 2.5.8 is unaffected: the `min-width`/`min-height` floor is what carries the
 * criterion and none of these touch it — which is exactly why the base rule
 * spells the floor with `min-*` rather than `width`/`height`.
 *
 * **The fix is per-file, not here**: give the rule the same
 * `:not([type='checkbox']):not([type='radio'])` carve-out that `app.css`'s
 * `.modal input` recipe and `routes/admin/privacy` already spell. Entries may
 * only ever be REMOVED; a new file introducing the idiom fails the ratchet
 * below by name.
 */
const GENERIC_INPUT_RECIPE_REACHES_CHECKBOX: Record<string, string> = {
	'lib/components/modals/BulkRecodeGLModal.svelte {.filters input}':
		'The GL-recode filter row recipe; the dialog also carries the ' +
		'"include AI fallback" toggle.',
	'lib/components/modals/CatalogModal.svelte {.form-grid input, .form-grid select, .form-grid textarea}':
		'Reaches the Active / Preferred toggles in the same grid.',
	'lib/components/modals/ContractModal.svelte {.form-grid input, .form-grid select, .form-grid textarea}':
		'Reaches the Not-to-exceed / Auto-renew toggles.',
	'lib/components/modals/ContractModal.svelte {.sub-form-grid input}':
		'The renew sub-form recipe, same shape.',
	'lib/components/modals/ExpenseModal.svelte {.form-grid input, .form-grid select, .form-grid textarea}':
		'Reaches the Reimbursable toggle.',
	'lib/components/modals/PolicyModal.svelte {.form-grid input}': 'Reaches the Active toggle.',
	'routes/expenses/+page.svelte {.attach-row input, .reject-row input}':
		'Inline attach / reject row recipe on a page that also renders row-select ' +
		'checkboxes.',
	'routes/expenses/+page.svelte {.report-form input, .report-form textarea}':
		'The new-report form recipe, same page.',
	'routes/organization/+page.svelte {input, select, textarea}':
		'A bare `input` recipe on a page whose panels are largely toggles. Ties ' +
		'with the global base on specificity, so which one wins is decided by ' +
		'stylesheet order rather than by intent.',
	'routes/organization/+page.svelte {.threshold-row input, .threshold-row textarea}':
		'The fraud-threshold row recipe, same page.',
	'routes/profile/+page.svelte {input}':
		'A bare `input` recipe on the page carrying the notification-preference ' +
		'checkboxes.',
	'routes/workflows/+page.svelte {.form-group input, .form-group textarea}':
		'The create-workflow form recipe on a page that also renders row-select ' +
		'checkboxes.'
};

const baseFindings = auditCheckboxBaseRule(allSources);
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

	it('finds the idiom it is meant to find', () => {
		// The scan's own canary against the live tree: if the parser broke, every
		// assertion here would go green by finding nothing at all.
		expect(overrides.map(findingKey)).toContain('routes/profile/+page.svelte {input}');
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

	it('adds no new file whose generic input recipe repaints a checkbox', () => {
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
