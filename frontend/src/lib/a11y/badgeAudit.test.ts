import { describe, expect, it } from 'vitest';
import { countByFile, findHandRolledBadgeRules } from './badgeAudit';
import { extractStyleBlocks, type StyleSource } from './cssAudit';

/**
 * Repo-wide ratchet on the tinted-badge conversion.
 *
 * `ui/Badge.svelte` owns the `background: var(--<tone>-tint); color:
 * var(--<tone>-on-tint)` recipe. 205 CSS rules hand-rolled it in 44 spellings of
 * the same five tones before it existed; roughly two thirds have moved onto the
 * primitive, and the rest move in **attributable tranches** — the shared tokens
 * standardise on alpha `.15`, so converting a `.1` or `.12` rule visibly
 * strengthens that badge, and landing them all at once would make any visual
 * complaint impossible to trace to a commit.
 *
 * That is what makes a ratchet the right guard rather than a hard zero. It:
 *
 *   - **cannot be satisfied by adding.** A new hand-rolled badge fails on the
 *     file it landed in, by name, with the selector.
 *   - **records the conversion as it happens.** Landing a tranche means editing
 *     the number down here, in the same commit, which is also the only place
 *     the remaining work is counted.
 *   - **holds the finished files at zero.** `/payments`, `/admin/webhooks`,
 *     `RequisitionModal` and `ExpenseModal` were converted in round 13; a
 *     regression there is a plain failure, not a loosened budget.
 *
 * The audit deliberately counts more than the seven files the follow-up names:
 * `chip` / `pill` / `tag` are the same capsule under other names, and the
 * recipe is respelled in components (`RunDetailModal`, `ScreeningBadge`,
 * `SupplierChatThread`) as readily as in routes.
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
 * Hand-rolled badge rules per file, as of the last tranche.
 *
 * **Only ever edit a number DOWNWARD** (or delete the entry at zero). A file
 * missing from this map must have none at all — which is how a brand-new page
 * is held to the primitive from its first commit.
 */
const BASELINE: Record<string, number> = {
	'lib/components/admin/RolesPanel.svelte': 3,
	'lib/components/admin/UsersPanel.svelte': 2,
	'lib/components/chat/SupplierChatThread.svelte': 3,
	'lib/components/marketing/Landing.svelte': 1,
	'lib/components/modals/InvoiceModal.svelte': 4,
	'lib/components/modals/PositivePayModal.svelte': 3,
	'lib/components/modals/RunDetailModal.svelte': 7,
	'lib/components/modals/VendorConsolidationModal.svelte': 1,
	'lib/components/modals/VendorStatementReconModal.svelte': 3,
	'lib/components/ui/DiscountTierBar.svelte': 1,
	// Its own smaller sentence-case metrics are deliberate (a badge that needs
	// different metrics is a different component, not a prop) — but it takes the
	// palette tokens, so it is a keeper rather than a conversion target.
	'lib/components/ui/ScreeningBadge.svelte': 3,
	'lib/components/workflow-builder/WorkflowCanvas.svelte': 2,
	'routes/billing/+page.svelte': 2,
	'routes/credit-memos/+page.svelte': 2,
	'routes/discounts/+page.svelte': 4,
	'routes/exceptions/+page.svelte': 3,
	'routes/expenses/+page.svelte': 10,
	'routes/goods-receipts/+page.svelte': 1,
	'routes/invoices/+page.svelte': 1,
	'routes/organization/+page.svelte': 1,
	// The two deliberate keeps from the round-13 tranche: `.discount-chip` is two
	// stacked lines, `.blocked-chip` wraps a localised sentence where `nowrap`
	// would break 320px reflow. Both took the palette tokens.
	'routes/payments/+page.svelte': 2,
	'routes/purchase-orders/+page.svelte': 3,
	'routes/requisitions/+page.svelte': 6,
	'routes/tax/+page.svelte': 3,
	'routes/vendor-statements/+page.svelte': 1,
	'routes/vendors/+page.svelte': 3,
	'routes/vendors/change-requests/+page.svelte': 3,
	'routes/workflows/+page.svelte': 1,
	'routes/workflows/[id]/+page.svelte': 3
};

/** Files a tranche took to zero. A rule reappearing here is a regression. */
const CONVERTED = [
	'routes/admin/webhooks/+page.svelte',
	// The dashboard's second spelling of `.overdue-badge` — /payments already
	// rendered the same flag through the primitive, so it shipped at two sizes
	// on two pages.
	'routes/+page.svelte',
	'lib/components/modals/RequisitionModal.svelte',
	'lib/components/modals/ExpenseModal.svelte'
];

const counts = countByFile(findHandRolledBadgeRules(sources));

describe('tinted-badge conversion ratchet', () => {
	it('scans a non-trivial set of stylesheets', () => {
		// A glob that silently matched nothing would make every assertion below
		// pass vacuously.
		expect(sources.length).toBeGreaterThan(50);
	});

	it('detects the recipe it is meant to detect', () => {
		// The audit's own regression test: a file everyone agrees still
		// hand-rolls must be found. Without it, breaking the parser turns this
		// whole suite green.
		expect(counts['routes/expenses/+page.svelte']).toBeGreaterThan(0);
	});

	it.each(CONVERTED)('%s stays on the shared primitive', (path) => {
		expect(counts[path] ?? 0).toBe(0);
	});

	it('no file grows a new hand-rolled badge rule', () => {
		const regressions = Object.entries(counts)
			.filter(([path, n]) => n > (BASELINE[path] ?? 0))
			.map(([path, n]) => `${path}: ${n} (baseline ${BASELINE[path] ?? 0})`);

		expect(
			regressions,
			'A badge-shaped rule that sets both a tinted background and a colour ' +
				'belongs on <Badge tone="…">, which owns that recipe — a caller names a ' +
				'tone and cannot spell it wrong. If this is a deliberate keep (its own ' +
				'metrics, a multi-line chip), take the palette tokens and raise the ' +
				'baseline with the reason.'
		).toEqual([]);
	});

	it('the baseline names no file that is already clean', () => {
		// Keeps the map honest as tranches land: a stale entry would quietly
		// re-authorise a hand-roll in a file that no longer has one.
		const stale = Object.keys(BASELINE).filter((path) => (counts[path] ?? 0) === 0);
		expect(stale, 'converted — drop these from BASELINE').toEqual([]);
	});
});
