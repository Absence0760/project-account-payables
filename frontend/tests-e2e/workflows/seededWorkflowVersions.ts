import { tenantPsql } from '../fixtures/helpers';

/**
 * Teardown for the `workflow_versions` rows a spec creates on the tenant's
 * SEEDED default definition.
 *
 * `PATCH /api/workflows/{id}` auto-snapshots the prior `steps_config` into
 * `workflow_versions` whenever the steps actually change
 * (`api/workflow_definitions.py::update_workflow`). Two specs here —
 * `invoice-routing` and `deactivation-snapshot` — reconfigure the seeded
 * default's approval step and then put it back, so each run leaves a pair of
 * rows behind. They are invisible: the `/workflows` list shows definitions,
 * not versions, and nothing but that definition's Version History modal reads
 * them, so the pile only ever grows. A local `e2e5` measured 0 → 5 → 9 across
 * two suite runs, and `e2e2` was at 14.
 *
 * This is deliberately NOT part of `deleteWorkflowsWhere` (decisions §127).
 * That owner sweeps by name prefix and carries an `is_default = false`
 * seatbelt precisely so no caller can reach a seeded row; the rows here hang
 * off a seeded row, which is the case it must refuse. Versions on
 * SPEC-CREATED definitions stay its job — it already deletes them — so the two
 * owners partition the table rather than overlap on it.
 *
 * It is also not a blanket "delete this definition's versions". A tenant may
 * legitimately carry versions that pre-date the run (a manual save, an earlier
 * restore), and decisions §122's rule applies: a spec owns the rows it
 * created, not the ones it found. The mark is a timestamp taken before the
 * spec touches anything, and only rows created after it are removed.
 *
 * There is no `DELETE /api/workflows/{id}/versions/{version_id}` — the builder
 * treats version history as append-only and exposes list / create / restore /
 * diff only — so this goes through `tenantPsql`, the same direct-SQL teardown
 * mechanism the sibling specs' `hardDeleteInvoice` uses. It is a CHILD-table
 * sweep, which `meta/teardown-guard.spec.ts` explicitly leaves to specs; only
 * `invoices`, `vendors` and `workflow_definitions` have owners.
 */

/** What the seeded default's version history looked like before the test ran. */
export interface SeededVersionMark {
	/** DB clock reading taken before the spec mutated anything. */
	readonly takenAt: string;
	/** How many versions the seeded default already carried at that moment. */
	readonly count: number;
}

/** Rows on the tenant's seeded default definition, in one place so the mark
 *  and the purge cannot drift apart on how "the seeded default" is spelled. */
const SEEDED_VERSIONS =
	'FROM workflow_versions v JOIN workflow_definitions d ON d.id = v.definition_id ' +
	'WHERE d.is_default = true';

/** Take the pre-test reading. Call from `beforeEach`, before anything PATCHes. */
export function markSeededWorkflowVersions(slug?: string): SeededVersionMark {
	const row = tenantPsql(
		`SELECT now()::text || '|' || (SELECT count(*) ${SEEDED_VERSIONS})::text`,
		slug
	).trim();
	const [takenAt, count] = row.split('|');
	return { takenAt, count: Number(count) };
}

/**
 * Remove exactly the versions this test added to the seeded default, then
 * prove it: the history is back to the size the mark recorded.
 *
 * The post-condition is the guard on this helper itself. If a future edit
 * writes seeded-default versions by some path the timestamp filter misses,
 * the teardown fails loudly here rather than quietly resuming the leak.
 */
export function purgeSeededWorkflowVersions(mark: SeededVersionMark, slug?: string): void {
	tenantPsql(
		`DELETE FROM workflow_versions v USING workflow_definitions d ` +
			`WHERE v.definition_id = d.id AND d.is_default = true ` +
			`AND v.created_at > '${mark.takenAt}'::timestamptz`,
		slug
	);
	const after = Number(tenantPsql(`SELECT count(*) ${SEEDED_VERSIONS}`, slug).trim());
	if (after !== mark.count) {
		throw new Error(
			`seeded workflow_versions teardown left ${after} row(s), expected ${mark.count} — ` +
				'a version was written to the seeded default by a path ' +
				'`purgeSeededWorkflowVersions` does not cover.'
		);
	}
}
