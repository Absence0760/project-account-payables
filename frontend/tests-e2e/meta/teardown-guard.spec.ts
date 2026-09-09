import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

/**
 * Source guard: no spec deletes an invoice, a vendor or a workflow definition
 * by hand.
 *
 * `invoices` is referenced by 16 foreign keys and **none of them cascade**, so
 * a bare `DELETE FROM invoices WHERE …` only succeeds while that invoice
 * happens to have no children. Around twenty specs relied on that, and one of
 * them (`invoices/upload-refetch-failure`) started failing teardown the moment
 * extraction began succeeding and writing line items — the invoice grew a child
 * the spec's hand-maintained delete list didn't know about.
 *
 * `vendors` is the same trap one table over: 17 foreign keys, only two of them
 * cascading, and seventeen specs each carrying its own partial child list
 * (`vendors/import-csv` knew about `sanctions_checks`, most knew about
 * nothing).
 *
 * `workflow_definitions` is the trap's other shape: three non-cascading
 * references and ten workflow specs carrying a byte-identical copy of the same
 * walk, differing only in the marker swept by — correct today, and ten places
 * to miss the next child in. All three tables now have exactly one owner in
 * `fixtures/helpers.ts`.
 *
 * Every call site goes through those owners; this guard is what stops the next
 * spec re-introducing the pattern, because the failure it causes is a teardown
 * error in an unrelated file weeks later.
 *
 * Detection is deliberately source-level rather than type-level: the SQL is a
 * template literal, so no compiler can see it. Comments are stripped first so
 * prose *about* the pattern (this file, and the helpers' own docstrings) doesn't
 * trip it.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const E2E_ROOT = join(HERE, '..');

/** The one file allowed to issue the statements — it is the implementation. */
const OWNER = join('fixtures', 'helpers.ts');

/** Files the scan skips, and why. Kept to exactly two: widening this set is
 *  how the pattern comes back. */
const EXEMPT = new Set([
	OWNER,
	// This file: its known-bad fixtures below are string literals, not comments,
	// so the detector correctly sees them.
	join('meta', 'teardown-guard.spec.ts')
]);

/** One guarded table: the statement to hunt for, the helper that owns it, how
 *  that helper is called, and why the hand-rolled version is a trap. The
 *  `signature` is spelled out because the three owners do NOT agree on it —
 *  `deleteWorkflowsWhere` takes a name prefix, not a WHERE clause body — and a
 *  failure message that guessed would send the next author down the wrong
 *  path. */
type GuardedTable = {
	readonly table: string;
	readonly helper: string;
	readonly signature: string;
	readonly why: string;
};

const GUARDED: readonly GuardedTable[] = [
	{
		table: 'invoices',
		helper: 'deleteInvoicesWhere',
		signature: 'deleteInvoicesWhere(predicate, slug?)',
		why:
			'`invoices` has 16 non-cascading foreign keys, so a bare ' +
			'`DELETE FROM invoices` only works until the invoice acquires a child ' +
			'the spec did not anticipate.'
	},
	{
		table: 'vendors',
		helper: 'deleteVendorsWhere',
		signature: 'deleteVendorsWhere(predicate, slug?)',
		why:
			'`vendors` has 17 foreign keys and only two of them cascade, so a bare ' +
			'`DELETE FROM vendors` only works until the vendor acquires a child ' +
			'the spec did not anticipate — a purchase order, a contract, a catalog, ' +
			'a sanctions check, a virtual card.'
	},
	{
		table: 'workflow_definitions',
		helper: 'deleteWorkflowsWhere',
		signature: 'deleteWorkflowsWhere(namePrefix, slug?)',
		why:
			'`workflow_definitions` has three non-cascading foreign keys — ' +
			'`workflow_versions`, `workflow_experiments` and `workflow_instances`, ' +
			'the last itself referenced by `workflow_steps` — so a bare ' +
			'`DELETE FROM workflow_definitions` only works until the definition ' +
			'acquires a child the spec did not anticipate, and the helper also ' +
			'carries the `is_default = false` seatbelt that keeps a marker typo ' +
			'away from the seeded default.'
	}
];

/** `DELETE FROM <table>`, in any casing, with any run of whitespace. */
function handRolledDelete(table: string): RegExp {
	return new RegExp(`delete\\s+from\\s+${table}\\b`, 'i');
}

/**
 * Strip `//` line and block comments so prose describing the anti-pattern is
 * not itself flagged. Crude by design — it can mangle a `//` inside a string
 * literal, which cannot produce a false NEGATIVE for the pattern we search for.
 */
export function stripComments(source: string): string {
	return source.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ');
}

/** Does this file issue a hand-rolled delete against `table`? */
export function hasHandRolledDelete(source: string, table: string): boolean {
	return handRolledDelete(table).test(stripComments(source));
}

function typescriptFiles(dir: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(dir, { withFileTypes: true })) {
		if (entry.name === 'node_modules' || entry.name === '.auth') continue;
		const full = join(dir, entry.name);
		if (entry.isDirectory()) found.push(...typescriptFiles(full));
		else if (entry.name.endsWith('.ts')) found.push(full);
	}
	return found;
}

test.describe('e2e teardown discipline', () => {
	for (const { table, helper, signature, why } of GUARDED) {
		test(`no spec deletes ${table} without going through ${helper}`, () => {
			const offenders = typescriptFiles(E2E_ROOT)
				.map((file) => relative(E2E_ROOT, file))
				.filter((file) => !EXEMPT.has(file))
				.filter((file) => hasHandRolledDelete(readFileSync(join(E2E_ROOT, file), 'utf8'), table));

			expect(
				offenders,
				`${why} Use \`${signature}\` from fixtures/helpers.ts, ` +
					'which owns the whole graph.'
			).toEqual([]);
		});
	}

	test('the helpers still own the statements they are exempted for', () => {
		// If either helper is ever gutted, its guard above would pass vacuously.
		// Pin that the owner really is the owner.
		const helperSource = readFileSync(join(E2E_ROOT, OWNER), 'utf8');
		for (const { table, helper } of GUARDED) {
			expect(hasHandRolledDelete(helperSource, table)).toBe(true);
			expect(helperSource).toContain(`export function ${helper}(`);
		}
	});

	test('the detector flags a known-bad file and clears a known-good one', () => {
		// A clean scan over clean files proves nothing about the detector, so
		// exercise it against fixtures that stand in for both outcomes.
		const bad = [
			"import { tenantPsql } from '../fixtures/helpers';",
			'test.afterEach(() => {',
			"\ttenantPsql(`DELETE FROM invoices WHERE id='${id}'`);",
			'});'
		].join('\n');
		const badLowercase = "sql(`delete from invoices where id = '${id}';`);";
		const badWrapped = 'tenantPsql(`DELETE\n\tFROM\n\tinvoices WHERE id=\'x\'`);';
		const good = [
			"import { deleteInvoicesWhere } from '../fixtures/helpers';",
			'// Never hand-roll a DELETE FROM invoices here — see the helper.',
			'/* DELETE FROM invoices is what this used to do. */',
			'test.afterEach(() => {',
			"\tdeleteInvoicesWhere(`id='${id}'`);",
			'});'
		].join('\n');

		expect(hasHandRolledDelete(bad, 'invoices')).toBe(true);
		expect(hasHandRolledDelete(badLowercase, 'invoices')).toBe(true);
		expect(hasHandRolledDelete(badWrapped, 'invoices')).toBe(true);
		expect(hasHandRolledDelete(good, 'invoices')).toBe(false);

		// The vendor half of the guard, including the two near-misses that must
		// NOT trip it: a different table whose name merely starts with `vendor`,
		// and the child-table sweeps a spec is still free to write.
		const badVendor = "tenantPsql(`DELETE FROM vendors WHERE name LIKE '${MARKER}%'`);";
		const goodVendor = [
			"import { deleteVendorsWhere } from '../fixtures/helpers';",
			"tenantPsql(`DELETE FROM vendor_users WHERE vendor_id='${id}'`);",
			"tenantPsql(`DELETE FROM vendor_statement_reconciliations WHERE id='${id}'`);",
			"deleteVendorsWhere(`id='${id}'`);"
		].join('\n');

		expect(hasHandRolledDelete(badVendor, 'vendors')).toBe(true);
		expect(hasHandRolledDelete(goodVendor, 'vendors')).toBe(false);

		// The workflow half. The near-miss that must NOT trip it is the child
		// sweep a spec is still free to write: `workflows/delete-safety` and
		// `workflows/bulk-delete` each wedge a definition with a synthetic
		// instance and clear it by id.
		const badWorkflow =
			"tenantPsql(`DELETE FROM workflow_definitions WHERE id IN (${doomed})`);";
		const goodWorkflow = [
			"import { deleteWorkflowsWhere } from '../fixtures/helpers';",
			"tenantPsql(`DELETE FROM workflow_instances WHERE id='${instanceId}'`);",
			'deleteWorkflowsWhere(MARKER);'
		].join('\n');

		expect(hasHandRolledDelete(badWorkflow, 'workflow_definitions')).toBe(true);
		expect(hasHandRolledDelete(goodWorkflow, 'workflow_definitions')).toBe(false);
	});
});
