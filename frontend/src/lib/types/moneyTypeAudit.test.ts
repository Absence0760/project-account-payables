import { describe, expect, it } from 'vitest';

import {
	countByFile,
	findMoneyShapedNumberFields,
	isMoneyShapedName
} from './moneyTypeAudit';

import type { Budget, BudgetSpend } from './budget';
import type { IntakeRequest } from './intake';
import type { PositivePayFile } from './positivePay';
import type { RecurringOccurrence, RecurringTemplate } from './recurring';

/**
 * Repo-wide ratchet on the money-typing conversion of `src/lib/types/`.
 *
 * `frontend/CLAUDE.md` § Money formatting forbids typing an API money field
 * `number`: it silently invites `.toFixed()`, `a - b` and `Math.max()` on
 * currency. Typed `MoneyAmount` / `MoneyString` the same expression is a
 * *compile error*, which must then be resolved with `parseMoneyForLayout`
 * (geometry) or `isPositiveAmount` / `isNegativeAmount` (a predicate) —
 * **never an `as` cast**, which would put the hazard straight back.
 *
 * This cannot land as one sweep, and that is the whole reason it is a ratchet:
 * a scan for money-shaped names returns ~105 fields of which most are NOT money
 * (`total` is a pagination row count on every list envelope, `budget` and
 * `remaining` are *token* counts on the assistant meter, `limit` is a report
 * row cap). There is no way to tell them apart by name, so each needs a
 * judgment and each genuine money field needs its call sites resolved. So it:
 *
 *   - **cannot be satisfied by adding.** A new `number`-typed money-shaped
 *     field fails on the file it landed in, by name, with the line number.
 *   - **records the conversion as it happens.** Landing a module means editing
 *     its number down here — or moving it into `CONVERTED` — in the same
 *     commit, which is also the only place the remaining work is counted.
 *   - **holds the finished modules at zero.** A regression in a converted
 *     module is a plain failure, not a loosened budget.
 *
 * The type-level half of the guarantee is at the bottom of this file: a set of
 * `@ts-expect-error` probes that FAIL `pnpm check` if arithmetic on any of the
 * converted modules' money fields ever starts compiling again.
 */

const RAW = import.meta.glob('/src/lib/types/*.ts', {
	query: '?raw',
	import: 'default',
	eager: true
}) as Record<string, string>;

const sources = Object.entries(RAW)
	.map(([path, source]) => [path.replace(/^\/src\//, ''), source] as const)
	.filter(([path]) => !path.endsWith('.test.ts') && !path.endsWith('moneyTypeAudit.ts'))
	.sort(([a], [b]) => a.localeCompare(b));

/**
 * The per-field judgments: money-SHAPED names that are not money.
 *
 * Keyed `file` → `Interface.field` → why. Every entry is a claim a reviewer can
 * check against the backend schema, which is the point — the alternative is a
 * name-based guess nobody can audit. A file may only enter {@link CONVERTED}
 * once every candidate in it is either retyped or judged here.
 */
const JUDGED_NOT_MONEY: Record<string, Record<string, string>> = {
	'lib/types/accessReview.ts': {
		'AccessReviewResponse.total': 'users reviewed — a head count, not money'
	},
	'lib/types/assistant.ts': {
		'ConversationListResponse.total': 'conversation row count',
		// `schemas/assistant.py::UsageResponse` types both `int`, beside
		// input_tokens / output_tokens / total_tokens. The assistant meter is
		// denominated in TOKENS; the money that meter later prices lives on the
		// billing surface, not here.
		'UsageResponse.budget': 'the per-org monthly TOKEN allowance, not a cash budget',
		'UsageResponse.remaining': 'tokens left in that allowance',
		'InvoiceListResult.total': 'row count of the assistant tool result',
		'PendingApprovalsResult.total': 'row count of the assistant tool result'
	},
	'lib/types/budget.ts': {
		'BudgetListResponse.total': 'pagination row count of the whole filtered set',
		// The allocation totals are on `BudgetSummary.by_currency`, grouped per
		// currency and typed `MoneyString` — this sits beside them and counts rows.
		'BudgetSummary.total': 'budget row count of the whole filtered set'
	},
	'lib/types/exceptionAgents.ts': {
		'AgentDecisionList.total': 'decision row count',
		'AgentStats.total_decisions': 'decisions the agents took — a count'
	},
	'lib/types/intake.ts': {
		'IntakeListResponse.total': 'pagination row count of the whole filtered set',
		'IntakeSummary.total': 'intake row count of the whole filtered set'
	},
	'lib/types/notification.ts': {
		'NotificationListResponse.total': 'notification row count'
	},
	'lib/types/positivePay.ts': {
		'PositivePayListResponse.total': 'pagination row count of the whole filtered set',
		'PositivePaySummary.total': 'file row count of the whole filtered set'
	},
	'lib/types/privacy.ts': {
		'DataSubjectRequestList.total': 'DSAR / erasure request row count'
	},
	'lib/types/recurring.ts': {
		'RecurringListResponse.total': 'pagination row count of the whole filtered set',
		'RecurringTemplateSummary.total': 'template row count of the whole filtered set',
		'RecurringHistory.total': 'generated-invoice row count for one template'
	},
	'lib/types/reports.ts': {
		// `ReportSpec.limit` is the report builder's hard ROW cap, independent of
		// pagination — nothing to do with a spend limit.
		'ReportSpec.limit': 'a row cap on the report result, not a spend limit'
	},
	'lib/types/vendor.ts': {
		'VendorStatusCounts.total': 'vendor row count behind the status chips',
		'VendorMergeResponse.total_reassigned': 'rows re-pointed by the merge — a count',
		'VendorChangeRequestPage.total': 'pagination row count',
		'VendorChangeRequestCounts.total': 'change-request row count'
	}
};

/**
 * Modules whose every money field is typed `MoneyAmount` / `MoneyString`.
 *
 * A candidate reappearing here is a regression, not a budget to raise. Four
 * were converted in this round (`budget` — the module the follow-up names as
 * the clearest case — plus `intake`, `positivePay` and `recurring`); the other
 * seven carry no money at all and are pinned so they cannot grow one untyped.
 */
const CONVERTED = [
	'lib/types/accessReview.ts',
	'lib/types/assistant.ts',
	'lib/types/budget.ts',
	'lib/types/exceptionAgents.ts',
	'lib/types/intake.ts',
	'lib/types/notification.ts',
	'lib/types/positivePay.ts',
	'lib/types/privacy.ts',
	'lib/types/recurring.ts',
	'lib/types/reports.ts',
	'lib/types/vendor.ts'
];

/**
 * `number`-typed money-shaped fields still awaiting a judgment, per module.
 *
 * **Only ever edit a number DOWNWARD** (or move the file into `CONVERTED` at
 * zero). A module missing from this map and from `CONVERTED` must have none —
 * which is how a brand-new type module is held to the rule from its first
 * commit.
 */
const BASELINE: Record<string, number> = {
	'lib/types/catalog.ts': 7,
	'lib/types/contract.ts': 12,
	'lib/types/discounts.ts': 14,
	'lib/types/expense.ts': 16,
	'lib/types/invoice.ts': 7,
	'lib/types/payment.ts': 2,
	'lib/types/requisition.ts': 7,
	'lib/types/vendorStatementRecon.ts': 10,
	'lib/types/workflow.ts': 6
};

const candidates = findMoneyShapedNumberFields(sources);
const unjudged = candidates.filter((c) => JUDGED_NOT_MONEY[c.file]?.[c.key] === undefined);
const counts = countByFile(unjudged);

describe('money-typing ratchet over src/lib/types', () => {
	it('scans a non-trivial set of type modules', () => {
		// A glob that silently matched nothing would make every assertion below
		// pass vacuously.
		expect(sources.length).toBeGreaterThan(30);
	});

	it('detects the shape it is meant to detect', () => {
		// The audit's own regression test: a module everyone agrees still carries
		// `number`-typed money must be found. Without it, breaking the parser
		// turns this whole suite green.
		expect(counts['lib/types/expense.ts']).toBeGreaterThan(0);
	});

	it.each(CONVERTED)('%s carries no number-typed money field', (path) => {
		const remaining = unjudged
			.filter((c) => c.file === path)
			.map((c) => `${c.key}: ${c.type} (line ${c.line})`);
		expect(
			remaining,
			`${path} is a CONVERTED module: every money field there is typed ` +
				'`MoneyAmount` / `MoneyString`. A new `number`-typed field is either ' +
				'money (retype it, and resolve the arithmetic with parseMoneyForLayout / ' +
				'isPositiveAmount — never an `as` cast) or it is not (add it to ' +
				'JUDGED_NOT_MONEY with the reason).'
		).toEqual([]);
	});

	it('no module grows a new number-typed money field', () => {
		const regressions = Object.entries(counts)
			.filter(([path, n]) => n > (BASELINE[path] ?? 0))
			.map(([path, n]) => `${path}: ${n} (baseline ${BASELINE[path] ?? 0})`);

		expect(
			regressions,
			'A money field typed `number` invites `a - b` / `Math.max()` on ' +
				'currency and the type system will not stop it. Type it `MoneyAmount` ' +
				'(endpoints that serialise a JSON number) or `MoneyString` (exact ' +
				'decimal strings). If the field is NOT money — a row count, a ' +
				'percentage, a token budget — record that judgment in ' +
				'JUDGED_NOT_MONEY rather than raising the baseline.'
		).toEqual([]);
	});

	it('the baseline names no module that is already clean', () => {
		// Keeps the map honest as modules land: a stale entry would quietly
		// re-authorise a `number`-typed money field in a converted module.
		const stale = Object.keys(BASELINE).filter((path) => (counts[path] ?? 0) === 0);
		expect(stale, 'converted — move these to CONVERTED and drop them here').toEqual([]);
	});

	it('every judgment still names a real field', () => {
		// A judgment left behind after the field was renamed or retyped would
		// silently pre-authorise a future field of the same name.
		const live = new Set(candidates.map((c) => `${c.file}::${c.key}`));
		const stale = Object.entries(JUDGED_NOT_MONEY).flatMap(([file, keys]) =>
			Object.keys(keys)
				.filter((key) => !live.has(`${file}::${key}`))
				.map((key) => `${file}::${key}`)
		);
		expect(stale, 'field is gone or no longer number-typed — drop the judgment').toEqual([]);
	});

	it('a converted module fails the moment a number-typed money field lands', () => {
		// Proves the guard rather than assuming it: this is exactly the diff the
		// ratchet exists to reject, run through the same scanner.
		const planted = findMoneyShapedNumberFields([
			[
				'lib/types/budget.ts',
				['export interface Budget {', '\tamount: number;', '}'].join('\n')
			]
		]);
		expect(planted).toHaveLength(1);
		expect(planted[0].key).toBe('Budget.amount');
		// ...and it is NOT covered by any existing judgment, so it reaches the
		// CONVERTED assertion above as a failure.
		expect(JUDGED_NOT_MONEY['lib/types/budget.ts']['Budget.amount']).toBeUndefined();
	});
});

describe('money-shaped name classification', () => {
	it('flags names that could denominate currency', () => {
		for (const name of [
			'amount',
			'total_amount',
			'unit_price',
			'spend_limit',
			'remaining_after',
			'projected_savings'
		]) {
			expect(isMoneyShapedName(name), name).toBe(true);
		}
	});

	it('does not flag counts, ratios or identifiers', () => {
		// The ~40 non-money fields a blanket sweep would have failed on.
		for (const name of [
			'total_count',
			'unconverted_count',
			'total_tokens',
			'total_requests',
			'total_rows',
			'amount_mismatches',
			'amount_variance_pct',
			'exception_count',
			'page_size',
			'invoice_id'
		]) {
			expect(isMoneyShapedName(name), name).toBe(false);
		}
	});

	it('ignores a field that is not number-typed', () => {
		const found = findMoneyShapedNumberFields([
			['lib/types/probe.ts', 'export interface X {\n\tamount: MoneyAmount;\n}']
		]);
		expect(found).toEqual([]);
	});

	it('catches a money union re-spelled inline instead of aliased', () => {
		// `MoneyAmount` is `string | number | null | undefined` — but it is the
		// NAME that makes arithmetic a type error at the call site, and an
		// inline re-spelling reads as deliberate while behaving identically.
		const found = findMoneyShapedNumberFields([
			['lib/types/probe.ts', 'export interface X {\n\tamount: string | number | null;\n}']
		]);
		expect(found.map((c) => c.key)).toEqual(['X.amount']);
	});
});

// ---------------------------------------------------------------------------
// Type-level guarantee.
//
// The scan above is a source check; THIS is the property it exists to protect.
// Every line below must be a compile error — `@ts-expect-error` fails the build
// if it is not — so retyping any of these fields back to `number` turns
// `pnpm check` red. Nothing here runs: the function is never called, and the
// imports are type-only.
// ---------------------------------------------------------------------------

function moneyArithmeticMustNotCompile(
	budget: Budget,
	spend: BudgetSpend,
	intake: IntakeRequest,
	file: PositivePayFile,
	template: RecurringTemplate,
	occurrence: RecurringOccurrence
) {
	// @ts-expect-error money is not a number — use `parseMoneyForLayout` for geometry.
	const scaled = budget.amount * 2;
	// @ts-expect-error never subtract two amounts client-side — read the backend's own figure.
	const headroom = spend.allocated - spend.committed;
	// @ts-expect-error a money comparison is `isNegativeAmount`, not `< 0`.
	const overspent = spend.remaining < 0;
	// @ts-expect-error a chart scale goes through `parseMoneyForLayout`.
	const scale = Math.max(intake.estimated_amount, 0);
	// @ts-expect-error money never round-trips through `.toFixed()`.
	const rendered = file.total_amount.toFixed(2);
	// @ts-expect-error cadence normalisation is the server's, in exact Decimal.
	const monthly = template.amount / 12;
	// @ts-expect-error summing amounts client-side is `sumMoney`, not `+`.
	const twoRuns = occurrence.amount + occurrence.amount;
	return { scaled, headroom, overspent, scale, rendered, monthly, twoRuns };
}
