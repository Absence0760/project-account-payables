import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

/**
 * Source guard: no spec deletes invoices by hand.
 *
 * `invoices` is referenced by 16 foreign keys and **none of them cascade**, so
 * a bare `DELETE FROM invoices WHERE …` only succeeds while that invoice
 * happens to have no children. Around twenty specs relied on that, and one of
 * them (`invoices/upload-refetch-failure`) started failing teardown the moment
 * extraction began succeeding and writing line items — the invoice grew a child
 * the spec's hand-maintained delete list didn't know about.
 *
 * `fixtures/helpers.ts::deleteInvoicesWhere` owns the whole child graph. Every
 * call site now goes through it; this guard is what stops the twentieth spec
 * re-introducing the pattern, because the failure it causes is a teardown
 * error in an unrelated file weeks later.
 *
 * Detection is deliberately source-level rather than type-level: the SQL is a
 * template literal, so no compiler can see it. Comments are stripped first so
 * prose *about* the pattern (this file, and the helper's own docstring) doesn't
 * trip it.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const E2E_ROOT = join(HERE, '..');

/** The one file allowed to issue the statement — it is the implementation. */
const OWNER = join('fixtures', 'helpers.ts');

/** Files the scan skips, and why. Kept to exactly two: widening this set is
 *  how the pattern comes back. */
const EXEMPT = new Set([
	OWNER,
	// This file: its known-bad fixtures below are string literals, not comments,
	// so the detector correctly sees them.
	join('meta', 'teardown-guard.spec.ts')
]);

/** `DELETE FROM invoices`, in any casing, with any run of whitespace. */
const HAND_ROLLED = /delete\s+from\s+invoices\b/i;

/**
 * Strip `//` line and block comments so prose describing the anti-pattern is
 * not itself flagged. Crude by design — it can mangle a `//` inside a string
 * literal, which cannot produce a false NEGATIVE for the pattern we search for.
 */
export function stripComments(source: string): string {
	return source.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ');
}

/** Does this file issue a hand-rolled invoice delete? */
export function hasHandRolledInvoiceDelete(source: string): boolean {
	return HAND_ROLLED.test(stripComments(source));
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
	test('no spec deletes invoices without going through deleteInvoicesWhere', () => {
		const offenders = typescriptFiles(E2E_ROOT)
			.map((file) => relative(E2E_ROOT, file))
			.filter((file) => !EXEMPT.has(file))
			.filter((file) => hasHandRolledInvoiceDelete(readFileSync(join(E2E_ROOT, file), 'utf8')));

		expect(
			offenders,
			'`invoices` has 16 non-cascading foreign keys, so a bare ' +
				'`DELETE FROM invoices` only works until the invoice acquires a child ' +
				'the spec did not anticipate. Use `deleteInvoicesWhere(predicate, slug?)` ' +
				'from fixtures/helpers.ts, which owns the whole graph.'
		).toEqual([]);
	});

	test('the helper still owns the statement it is exempted for', () => {
		// If `deleteInvoicesWhere` is ever gutted, the guard above would pass
		// vacuously. Pin that the owner really is the owner.
		const helper = readFileSync(join(E2E_ROOT, OWNER), 'utf8');
		expect(hasHandRolledInvoiceDelete(helper)).toBe(true);
		expect(helper).toContain('export function deleteInvoicesWhere(');
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

		expect(hasHandRolledInvoiceDelete(bad)).toBe(true);
		expect(hasHandRolledInvoiceDelete(badLowercase)).toBe(true);
		expect(hasHandRolledInvoiceDelete(badWrapped)).toBe(true);
		expect(hasHandRolledInvoiceDelete(good)).toBe(false);
	});
});
