/**
 * Source scanner behind the money-typing ratchet (`moneyTypeAudit.test.ts`).
 *
 * The rule it enforces is `frontend/CLAUDE.md` § Money formatting: **never type
 * an API money field `number`**. A `number` field silently invites `.toFixed()`,
 * `a - b` and `Math.max()` on currency, which is exactly the float arithmetic
 * the Decimal invariant exists to prevent — and the type system says nothing.
 * Typed `MoneyAmount` / `MoneyString`, the same expression is a compile error
 * that has to be resolved with `parseMoneyForLayout` (geometry) or
 * `isPositiveAmount` / `isNegativeAmount` (a predicate), never a cast.
 *
 * **Why this is a scanner and not a lint rule.** A grep for money-shaped names
 * across `src/lib/types/` returns ~150 fields and MOST OF THEM ARE NOT MONEY:
 * `total` is a pagination row count on every `*ListResponse`, `budget` and
 * `remaining` are *token* counts on the assistant's usage meter, `limit` is a
 * report row cap. Nothing distinguishes them from a real allocation by name.
 * So the scanner deliberately reports *candidates* and the test file carries
 * the per-field judgments — each one written down once, with its reason, where
 * a reviewer can see it.
 *
 * Two name filters do the mechanical part, so the judgment list only has to
 * cover genuinely ambiguous names:
 *
 * - {@link MONEY_SHAPED} — a name that could denominate money.
 * - {@link NEVER_MONEY} — a suffix/segment vocabulary this codebase never uses
 *   for a currency amount (`*_count`, `*_pct`, `*_days`, `*_tokens`, `*_rows`,
 *   `*_id`, …). A count is not money no matter what it counts.
 *
 * Scope is `src/lib/types/*.ts` — the API shape modules. Inline response types
 * declared inside a `.svelte` file are out of scope here (they are caught by
 * review, and by `pnpm check` once the shared type they consume is converted).
 *
 * Pure — no `$state`, no `fetch`, no browser globals — so it runs under the
 * plain-Node vitest config, like `a11y/badgeAudit.ts`.
 */

/** One `number`-typed field whose NAME could denominate money. */
export interface MoneyFieldCandidate {
	/** Path as globbed, e.g. `lib/types/budget.ts`. */
	file: string;
	/** Enclosing `interface` / `type` name, or `?` if it could not be resolved. */
	container: string;
	field: string;
	/** The declared type text, e.g. `number | null`. */
	type: string;
	/** 1-based line number, for the failure message. */
	line: number;
	/** `<container>.<field>` — the key the judgment list is written against. */
	key: string;
}

/**
 * Names that could denominate a currency amount.
 *
 * Matched on whole `_`-separated segments so `amount_mismatches` hits on
 * `amount` (and is then excluded by {@link NEVER_MONEY} on `mismatches`),
 * while `formatted` does not hit on `mat`.
 */
const MONEY_SHAPED =
	/(^|_)(amount|amounts|total|totals|subtotal|balance|price|cost|value|spend|savings|saving|fee|fees|rebate|rebates|discount|budget|allocated|committed|actual|remaining|paid|outflow|inflow|opening|closing|revenue|threshold|limit|advance|net|gross|charge|proration|credit|debit|payout|due|principal|interest)(_|$)/;

/**
 * Segments this codebase never uses for a currency amount.
 *
 * Every one of these is a COUNT, a RATIO or an IDENTIFIER — `total_count`,
 * `amount_variance_pct`, `total_tokens`, `total_rows`, `amount_mismatches`.
 * Excluding them mechanically keeps the hand-written judgment list to the
 * genuinely ambiguous names (a bare `total`, a bare `budget`) instead of
 * drowning it.
 */
const NEVER_MONEY =
	/(^|_)(count|counts|days|pct|percent|percentage|tokens|requests|rows|mismatches|id|ids|index|size|page|pages|rate|score|sample|version|hours|minutes|seconds|attempts|retries)(_|$)/;

/** A `name: type;` property line inside an interface / object type. */
const PROPERTY = /^\s*(?:readonly\s+)?([A-Za-z_$][A-Za-z0-9_$]*)\??:\s*([^;]+);\s*$/;

/** An `interface X` / `type X =` header, used to attribute a property. */
const CONTAINER = /^\s*(?:export\s+)?(?:declare\s+)?(?:interface|type)\s+([A-Za-z0-9_$]+)/;

/** Does this field NAME look like it denominates money? */
export function isMoneyShapedName(name: string): boolean {
	return MONEY_SHAPED.test(name) && !NEVER_MONEY.test(name);
}

/**
 * Every `number`-typed, money-shaped field across the given type modules.
 *
 * A field counts as `number`-typed when `number` appears as a standalone word
 * in its declared type — so `number`, `number | null` and `number | string`
 * all match, and `MoneyAmount` (which is `string | number | null | undefined`
 * *behind a name*) does not. That is the point: the alias is what makes
 * arithmetic a type error, so re-spelling the union inline would defeat it and
 * is caught here.
 */
export function findMoneyShapedNumberFields(
	sources: Iterable<readonly [path: string, source: string]>
): MoneyFieldCandidate[] {
	const found: MoneyFieldCandidate[] = [];
	for (const [file, source] of sources) {
		let container = '?';
		source.split('\n').forEach((line, i) => {
			const header = CONTAINER.exec(line);
			if (header) container = header[1];
			const property = PROPERTY.exec(line);
			if (!property) return;
			const [, field, type] = property;
			if (!/\bnumber\b/.test(type)) return;
			if (!isMoneyShapedName(field)) return;
			found.push({
				file,
				container,
				field,
				type: type.trim(),
				line: i + 1,
				key: `${container}.${field}`
			});
		});
	}
	return found;
}

/** `{file: candidate count}`, for the per-file ratchet. */
export function countByFile(candidates: Iterable<MoneyFieldCandidate>): Record<string, number> {
	const counts: Record<string, number> = {};
	for (const c of candidates) counts[c.file] = (counts[c.file] ?? 0) + 1;
	return counts;
}
