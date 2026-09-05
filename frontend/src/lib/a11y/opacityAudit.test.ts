import { describe, expect, it } from 'vitest';
import { findingKey, findOpacityFadeRules } from './opacityAudit';
import { extractStyleBlocks, type StyleSource } from './cssAudit';

/**
 * Repo-wide guard on de-emphasising text with `opacity`.
 *
 * `opacity` is GROUP opacity: it composites an element's whole subtree — text,
 * status badges and all — down onto the surface behind it. Used to say "this
 * row is paused", it fades colours that were each calibrated against that
 * surface at full strength, and it does so hardest to the colours that were
 * already quiet. Measured on `--surface`:
 *
 *     --text            @0.6 → 5.65:1   @0.5 → 4.34:1
 *     --text-muted      @0.6 → 2.77:1   @0.5 → 2.33:1
 *     a tinted <Badge>  @0.6 → 2.78–2.93:1
 *
 * So the one cell that explains why a row is faded — its status pill — became
 * the least readable thing in it. `/admin/api-keys` had already noticed half of
 * that and carved `:not(.status-col)` out of its fade; `/admin/webhooks` had
 * not. Both are now the shared `.row-muted` recipe in `app.css`, which names
 * `--text-muted` (5.38:1) and leaves any descendant that sets its own colour
 * at that colour's own full strength.
 *
 * **Neither existing guard could see this.** `cssAudit` measures a rule against
 * its OWN declarations, and the rule spending the contrast declares no colour —
 * the colours it ruins belong to descendants, often in another file. `axe`
 * measures what a listed route renders, and only if an inactive row happens to
 * be on screen. This scan closes that seam by reporting the idiom rather than a
 * ratio: for a text-bearing element the answer is always a muted token, never a
 * kinder alpha.
 *
 * It is a ratchet, not a hard zero, for the same reason `badgeAudit` is: the
 * two remaining sites are on money routes being edited concurrently, and a
 * whole-table visual change landing in the same commit as unrelated work makes
 * either one unattributable.
 */

const RAW = import.meta.glob('/src/**/*.{svelte,css}', {
	query: '?raw',
	import: 'default',
	eager: true
}) as Record<string, string>;

const sources: StyleSource[] = Object.entries(RAW)
	.map(([path, source]) => [path.replace(/^\/src\//, ''), source] as const)
	.sort(([a], [b]) => a.localeCompare(b))
	.flatMap(([path, source]) => extractStyleBlocks(path, source));

/**
 * Rules that fade something carrying **no text**, which is a markup fact the
 * CSS scan cannot establish for itself. Every entry states what the element is
 * and why the fade is sound; an entry with no reason is not an entry.
 *
 * Add one ONLY for a genuinely text-free element. "It looks fine" is what the
 * 2.77:1 rows looked like.
 */
const NON_TEXT_ALLOWLIST: Record<string, string> = {
	'lib/components/layout/Sidebar.svelte {.nav-group-divider}':
		'A 1px empty <div> separating nav groups. Purely decorative under 1.4.11.',
	'lib/components/ui/SortableHeader.svelte {.sort-icon}':
		'An aria-hidden sort glyph inside a labelled button; the state is also on ' +
		'the <th> as aria-sort. Measured 3.45:1 (inactive, --text-muted on --bg) ' +
		'and 3.53:1 (active, --accent) — both clear the 3:1 non-text bar.',
	'lib/components/ui/Toast.svelte {.toast-dismiss}':
		'An aria-hidden × SVG inside a button carrying its own aria-label. ' +
		'Measured 4.15:1 (error tone, the worst of the four) against its toast ' +
		'background — over the 3:1 non-text bar.',
	'routes/+page.svelte {.vendor-bar}':
		'An empty <div> chart bar; the figure it depicts is rendered as adjacent text.',
	'routes/+page.svelte {.trend-bar}':
		'An empty <div> chart bar; the figure it depicts is in an adjacent title + label.',
	'lib/components/marketing/Landing.svelte {.mock-bg-1, .mock-bg-2}':
		'Empty <div> background blobs in the hero mock.',
	'lib/components/marketing/Landing.svelte {.mock-bg-2}':
		'The same two blobs; second rule adjusts one of them.',
	'lib/components/marketing/Landing.svelte {.step-num}':
		'A watermark ordinal behind a "how it works" card, faded to 12% — and ' +
		'carrying aria-hidden, so the decoration claim is made in the markup ' +
		'rather than assumed here. The ordinal is already in the cards\' reading ' +
		'order, so the numeral conveys nothing the fade could cost.'
};

/**
 * Text de-emphasis still spelled as a fade, by `path {selector}`, with the
 * reason it has not moved yet.
 *
 * **Only ever remove entries.** A new one fails the ratchet below by name. To
 * clear one, give the row `class:row-muted` and delete its local `opacity`
 * rule — `app.css` `.grid-container tr.row-muted td:not(.actions)` is the
 * shared recipe and the only sanctioned answer.
 */
const PENDING_CONVERSION: Record<string, string> = {
	'routes/credit-memos/+page.svelte {tr.applied td, tr.void td}':
		'An applied / voided memo row. Same fix as the admin rows; deferred only ' +
		'because this route was being edited concurrently by another change.',
	'routes/payments/+page.svelte {.row-blocked}':
		'A queue row blocked by an unresolved exception. Its blocked-reason chip ' +
		'is the cell a reader most needs, and the 0.72 fade is what dims it. ' +
		'Deferred with the same concurrency reason as above.'
};

/** Files a conversion took to zero. A fade reappearing here is a regression. */
const CONVERTED = [
	'app.css',
	'lib/components/admin/UsersPanel.svelte',
	'routes/admin/webhooks/+page.svelte',
	'routes/admin/api-keys/+page.svelte'
];

const findings = findOpacityFadeRules(sources);
const keys = findings.map(findingKey);

describe('opacityAudit — the scanner', () => {
	const scan = (css: string) => findOpacityFadeRules([{ path: 'fixture.css', css }]);

	it('flags a row de-emphasised with opacity', () => {
		// The known-bad fixture: the exact rule that rendered /admin/webhooks'
		// paused rows at 2.93:1. Without this the whole suite could go green on
		// a scanner that finds nothing.
		expect(scan('tr.inactive td:not(.actions) { opacity: 0.6; }')).toEqual([
			{ path: 'fixture.css', selector: 'tr.inactive td:not(.actions)', opacity: 0.6 }
		]);
	});

	it('flags a fade written as a percentage, and takes the last declaration', () => {
		expect(scan('.stale { opacity: 60%; }')[0].opacity).toBeCloseTo(0.6);
		expect(scan('.stale { opacity: 0.4; opacity: 0.8; }')[0].opacity).toBeCloseTo(0.8);
	});

	it('ignores a fade that is a hide or a reset', () => {
		expect(scan('.gone { opacity: 0; } .back { opacity: 1; }')).toEqual([]);
		// A rule that fades and then resets renders unfaded.
		expect(scan('.reset { opacity: 0.5; opacity: 1; }')).toEqual([]);
	});

	it('ignores an inactive user-interface component (WCAG 1.4.3 exemption)', () => {
		expect(scan('.btn:disabled { opacity: 0.5; }')).toEqual([]);
		expect(scan('.opt:has(input:disabled) { opacity: 0.6; }')).toEqual([]);
		expect(scan('.intake input:disabled + span { opacity: 0.6; }')).toEqual([]);
		expect(scan('.node.disabled { opacity: 0.5; }')).toEqual([]);
		expect(scan('.upload-btn.uploading { opacity: 0.6; }')).toEqual([]);
	});

	it('ignores a transient pointer state', () => {
		// Measured: white on --accent-strong at 0.9 is 5.08:1 over --surface,
		// and the worst of the family (--success-strong at 0.85) is 4.72:1.
		expect(scan('.btn-save:hover:not(:disabled) { opacity: 0.9; }')).toEqual([]);
	});

	it('ignores keyframe steps and pseudo-elements', () => {
		expect(scan('@keyframes blink { 0%, 80%, 100% { opacity: 0.3; } }')).toEqual([]);
		expect(scan("input[type='date']::-webkit-calendar-picker-indicator { opacity: 0.6; }")).toEqual(
			[]
		);
	});

	it('does not mistake an unrelated opacity-adjacent property for a fade', () => {
		expect(scan('.x { transition: opacity 0.12s; }')).toEqual([]);
		expect(scan('.y { background: rgba(0, 0, 0, 0.5); }')).toEqual([]);
	});
});

describe('opacity text de-emphasis ratchet', () => {
	it('scans a non-trivial set of stylesheets', () => {
		// A glob that silently matched nothing would make every assertion below
		// pass vacuously.
		expect(sources.length).toBeGreaterThan(50);
	});

	it('finds the idiom it is meant to find', () => {
		// The audit's own regression test against the live tree: at least one
		// known-pending site must still be reported. Breaking the parser would
		// otherwise turn this suite green.
		expect(keys).toContain('routes/payments/+page.svelte {.row-blocked}');
	});

	it.each(CONVERTED)('%s no longer fades text with opacity', (path) => {
		expect(findings.filter((f) => f.path === path).map(findingKey)).toEqual([]);
	});

	it('no new element is de-emphasised with opacity', () => {
		const known = new Set([
			...Object.keys(NON_TEXT_ALLOWLIST),
			...Object.keys(PENDING_CONVERSION)
		]);
		expect(
			keys.filter((key) => !known.has(key)),
			'`opacity` composites an element and its whole subtree onto the surface ' +
				'behind it, so it fades every descendant colour — including a status ' +
				"badge's calibrated pair — well below 4.5:1. De-emphasise a row with " +
				'`class:row-muted` (app.css) or a muted colour token instead. If the ' +
				'element genuinely carries no text, add it to NON_TEXT_ALLOWLIST with ' +
				'the reason.'
		).toEqual([]);
	});

	it('neither list names a rule that is already gone', () => {
		// Keeps both maps honest: a stale entry would quietly re-authorise a fade
		// in a file that no longer has one.
		const live = new Set(keys);
		const stale = [...Object.keys(NON_TEXT_ALLOWLIST), ...Object.keys(PENDING_CONVERSION)].filter(
			(key) => !live.has(key)
		);
		expect(stale, 'resolved — drop these entries').toEqual([]);
	});
});
